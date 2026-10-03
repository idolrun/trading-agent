"""`apply`: ONE Postgres transaction per message event (TRD §2.1, database-schema.md T3).

Everything that needs I/O (context, interpretation, market data, risk) happened before; this
only writes: signals, links, provider ledger, cards, timers, supersession, inbox status and
NOTIFY. IDs are deterministic, so a re-run after a crash writes nothing twice (TRD §9).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.alerts import repo as alerts
from app.alerts.models import AlertKind, Severity
from app.cards import repo as cards
from app.cards.models import (
    Card,
    CardAction,
    CardActor,
    CardReason,
    CardSource,
    CardState,
    CardType,
)
from app.cards.validity import ENTRY_ACTIONS, supersedes
from app.db import notify
from app.decide.types import ProposedAction
from app.dispatcher.types import ProcessingContext
from app.inbox import repo as inbox
from app.inbox.envelope import EventKey
from app.inbox.models import InboxStatus, Stage, StageTiming
from app.interpret.models import Interpreter, LinkOutcome, MessageLink, MsgType, Signal
from app.interpret.signal import ParsedSignal, SignalFields
from app.ledger import repo as ledger
from app.ledger.correlation import LinkDecision
from app.ledger.models import ProviderPosition
from app.ledger.positions import PositionUpdate
from app.logging import bind
from app.timers import repo as timers
from app.timers.models import TimerKind

NAMESPACE = uuid.UUID("0f6a9d3e-2b1c-4f5e-8a7d-3c9b1e2f4a60")


def signal_id_for(key: EventKey, idx: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"signal:{key.message_id}:{key.event_type}:{key.event_seq}:{idx}")


def position_id_for(signal_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"position:{signal_id}")


class BookChanged(Exception):
    """The provider's book moved between planning and apply: retry the event."""


@dataclass(frozen=True)
class Correction:
    """F038: this signal corrects an earlier message (an edit event or a `correction`)."""

    target_signal_ids: tuple[uuid.UUID, ...]  # the corrected message's signals
    position_id: uuid.UUID | None  # the provider position it corrects (None: not an opening)
    original_text: str | None
    lot_pnl: Decimal | None  # live P&L of the user's lot, for the after-trade alert


@dataclass(frozen=True)
class SignalOutcome:
    """One signal, fully decided before the transaction."""

    idx: int
    signal_id: uuid.UUID
    parsed: ParsedSignal
    link: LinkDecision | None
    position_id: uuid.UUID | None  # linked, or the new position this signal opens
    update: PositionUpdate | None  # provider-ledger change (None = none)
    lot_id: uuid.UUID | None
    cards: list[ProposedAction]
    correction: Correction | None = None
    reentry_of: uuid.UUID | None = None  # F072: the new position re-enters this one


@dataclass(frozen=True)
class ApplyInput:
    ctx: ProcessingContext
    interpreter: Interpreter
    book_version: dict[uuid.UUID, datetime]  # position id -> updated_at the plan was built on
    outcomes: list[SignalOutcome]
    model_call_id: int | None = None  # the Jev call / LLM attempt that produced the signals
    hold_until: datetime | None = None  # off-hours (7.7): cards wait for this open (F039)
    # F078: how interpretation ended and why (inbox.interpret_outcome / interpret_reasons)
    outcome: str | None = None
    trace: dict[str, Any] | None = None


@dataclass
class ApplyOutcome:
    card_ids: list[uuid.UUID] = field(default_factory=list)
    superseded_card_ids: list[uuid.UUID] = field(default_factory=list)
    created: bool = True  # False = this event was applied before (idempotent re-run)


async def apply_event(db: AsyncSession, inp: ApplyInput) -> ApplyOutcome:
    """Call inside one transaction (the caller commits)."""
    env = inp.ctx.envelope
    locked = await ledger.lock_open_book(db, env.lane, env.posted_at)
    if {p.provider_position_id: p.updated_at for p in locked} != inp.book_version:
        raise BookChanged(f"lane {env.lane} book changed during processing")
    rows: dict[uuid.UUID, ProviderPosition] = {p.provider_position_id: p for p in locked}
    result = ApplyOutcome()

    if inp.outcomes and not await _insert_signal(db, inp, inp.outcomes[0]):
        result.created = False  # the caller still starts approval graphs for these cards
        result.card_ids = await _card_ids_of(db, [o.signal_id for o in inp.outcomes])
    else:
        for o in inp.outcomes[1:]:
            await _insert_signal(db, inp, o)
        for o in inp.outcomes:
            with bind(signal_id=str(o.signal_id)):
                await _apply_signal(db, inp, o, rows, result)

    stale = inp.ctx.stale and any(
        o.parsed.msg_type in (MsgType.ENTRY, MsgType.STOCK_ENTRY, MsgType.ADD) for o in inp.outcomes
    )
    await inbox.mark(
        db,
        env.key,
        InboxStatus.STALE if stale else InboxStatus.DONE,
        late=inp.ctx.late,
        interpret_outcome=inp.outcome,
        interpret_reasons=inp.trace,
    )
    if result.created and result.card_ids:
        await record_stage(db, env.key, inp.ctx.attempt, Stage.CARD)
    return result


async def _card_ids_of(db: AsyncSession, signal_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(col(Card.card_id))
        .where(col(Card.signal_id).in_(signal_ids))
        .order_by(col(Card.created_at), col(Card.card_id))
    )
    return list(rows.all())


async def _apply_signal(
    db: AsyncSession,
    inp: ApplyInput,
    o: SignalOutcome,
    rows: dict[uuid.UUID, ProviderPosition],
    result: ApplyOutcome,
) -> None:
    env = inp.ctx.envelope
    if o.update is not None:
        assert o.position_id is not None  # noqa: S101 — an update always has its position
        rows[o.position_id] = await ledger.record(
            db,
            provider_id=env.provider_id,
            row=rows.get(o.position_id),
            update=o.update,
            signal_id=o.signal_id,
            channel_id=env.channel_id,
            position_id=o.position_id,
            reentry_of=o.reentry_of,
        )
    exists = o.position_id is not None and o.position_id in rows
    if o.link is not None and (
        exists or o.link.outcome in (LinkOutcome.AMBIGUOUS, LinkOutcome.NONE)
    ):
        db.add(
            MessageLink(
                signal_id=o.signal_id,
                provider_id=env.provider_id,
                provider_position_id=o.position_id if exists else None,
                outcome=o.link.outcome,
                method=o.link.method,
                confidence=o.link.confidence,
                candidate_position_ids=list(o.link.candidate_position_ids),
                evidence=o.link.evidence,
            )
        )
        await db.flush()

    if o.correction is not None:  # F038: replaces supersession and plain card creation
        await _apply_correction(db, inp, o, o.correction, result)
        return

    if exists and o.position_id is not None and supersedes(o.parsed.msg_type, CardAction.ENTRY):
        for card in await cards.pending_on_position(db, o.position_id, ENTRY_ACTIONS):
            await cards.transition(
                db,
                card,
                CardState.INVALIDATED,
                CardReason.SUPERSEDED,
                CardActor.SYSTEM,
                detail={"by_signal_id": str(o.signal_id)},
            )
            result.superseded_card_ids.append(card.card_id)
            await notify(db, "cards", _event("card.updated", card.card_id))

    if inp.hold_until is not None and o.cards:  # C59: ledger now, the card at the open
        await timers.schedule(
            db,
            TimerKind.OFFHOURS_RELEASE,
            inp.hold_until,
            f"release:{env.key.message_id}:{env.key.event_type}:{env.key.event_seq}",
            event=env.key,
        )
        return
    for proposal in o.cards:
        card_id = await insert_card(
            db,
            proposal,
            key=f"{o.signal_id}:{proposal.action}",
            source=CardSource.SIGNAL,
            signal_id=o.signal_id,
            provider_id=env.provider_id,
            position_id=o.position_id if exists else None,
            lot_id=o.lot_id,
            posted_at=env.posted_at,
        )
        if card_id is not None:
            result.card_ids.append(card_id)


async def _apply_correction(
    db: AsyncSession, inp: ApplyInput, o: SignalOutcome, c: Correction, result: ApplyOutcome
) -> None:
    """PRD 7.5 / AC-5. Before the user trades: the pending card is invalidated and the
    corrected interpretation becomes a fresh card. After: alert only, no card; the lot keeps
    its own contract. Neither: the provider ledger was corrected above, nothing else."""
    env = inp.ctx.envelope
    targets = await cards.of_signals(db, c.target_signal_ids)
    if c.position_id is not None:
        targets += await cards.pending_on_position(db, c.position_id, ENTRY_ACTIONS)
    traded = any(t.state in (CardState.APPROVED, CardState.SUBMITTED) for t in targets) or (
        c.position_id is not None and await ledger.has_lot(db, c.position_id)
    )
    if traded:
        await raise_alert(
            db,
            AlertKind.LATE_CORRECTION,
            Severity.WARNING,
            "Provider corrected a message after you traded; follow-ups now refer to the "
            "corrected contract. Your lot keeps what you hold. No action proposed.",
            details={
                "original_text": c.original_text,
                "correction_text": env.text,
                "lot_pnl": str(c.lot_pnl) if c.lot_pnl is not None else None,
            },
            dedupe_key=f"late_correction:{o.signal_id}",
            message_id=env.key.message_id,
        )
        return
    pending = {t.card_id: t for t in targets if t.state == CardState.PENDING}
    for card in pending.values():
        await cards.transition(
            db,
            card,
            CardState.INVALIDATED,
            CardReason.CORRECTION,
            CardActor.SYSTEM,
            detail={"by_signal_id": str(o.signal_id)},
        )
        result.superseded_card_ids.append(card.card_id)
        await notify(db, "cards", _event("card.updated", card.card_id))
    if not pending:
        return  # nothing pending and nothing traded: the ledger correction is the whole effect
    for proposal in o.cards:  # needs fresh approval (C48)
        card_id = await insert_card(
            db,
            proposal,
            key=f"{o.signal_id}:{proposal.action}",
            source=CardSource.SIGNAL,
            signal_id=o.signal_id,
            provider_id=env.provider_id,
            position_id=o.position_id,
            lot_id=o.lot_id,
            posted_at=env.posted_at,
        )
        if card_id is not None:
            result.card_ids.append(card_id)


async def raise_alert(
    db: AsyncSession,
    kind: AlertKind,
    severity: Severity,
    message: str,
    *,
    details: dict[str, object],
    dedupe_key: str,
    message_id: int | None = None,
) -> None:
    """An alert inside the caller's transaction (atomic with the change it reports)."""
    alert_id = await alerts.insert_alert(
        db,
        kind=kind,
        severity=severity,
        message=message,
        details=details,
        dedupe_key=dedupe_key,
        card_id=None,
        lot_id=None,
        order_id=None,
        message_id=message_id,
    )
    if alert_id is not None:
        await notify(db, "alerts", str(alert_id))


async def insert_card(
    db: AsyncSession,
    proposal: ProposedAction,
    *,
    key: str,
    source: CardSource,
    signal_id: uuid.UUID | None,
    provider_id: int | None,
    position_id: uuid.UUID | None,
    lot_id: uuid.UUID | None,
    posted_at: datetime,
) -> uuid.UUID | None:
    """One card + its expiry timer + NOTIFY; None = it exists already (idempotent re-run).
    Shared by apply, re-link approval (F037) and the off-hours release (F039)."""
    disabled = proposal.card_type == CardType.DISABLED
    card = Card(
        card_id=cards.card_id_for(key),
        dedupe_key=key,
        source=source,
        signal_id=signal_id,
        action=proposal.action,
        card_type=proposal.card_type,
        state=CardState.DISABLED if disabled else CardState.PENDING,
        provider_id=provider_id,
        provider_position_id=position_id,
        lot_id=lot_id,
        proposal=proposal.model_dump(mode="json"),
        posted_at=posted_at,
        valid_until=proposal.valid_until,
        disabled_reason=proposal.disabled_reason,
    )
    if not await cards.insert(db, card):
        return None
    if not disabled and card.valid_until is not None:
        await timers.schedule(
            db,
            TimerKind.CARD_EXPIRY,
            card.valid_until,
            f"card_expiry:{card.card_id}",
            card_id=card.card_id,
        )
    await notify(db, "cards", _event("card.created", card.card_id))
    return card.card_id


async def _insert_signal(db: AsyncSession, inp: ApplyInput, o: SignalOutcome) -> bool:
    env, ctx = inp.ctx.envelope, inp.ctx
    inserted = await db.scalar(
        pg_insert(Signal)
        .values(
            signal_id=o.signal_id,
            message_id=env.key.message_id,
            event_type=env.key.event_type,
            event_seq=env.key.event_seq,
            idx=o.idx,
            provider_id=env.provider_id,
            msg_type=o.parsed.msg_type,
            instrument=o.parsed.instrument,
            fields=o.parsed.fields.model_dump(mode="json", exclude_none=True),  # re-parseable
            field_sources={
                k: v.model_dump(mode="json", exclude_none=True)
                for k, v in o.parsed.field_sources.items()
            },
            interpreter=inp.interpreter,
            model_call_id=inp.model_call_id,
            stale=ctx.stale,
            late=ctx.late,
            schema_version=1,
        )
        .on_conflict_do_nothing()
        .returning(col(Signal.signal_id))
    )
    return inserted is not None


def stored_signal(sig: Signal) -> ParsedSignal:
    """A `signals` row back as the contract it was written from (fields keep span kinds)."""
    return ParsedSignal(
        msg_type=sig.msg_type,
        instrument=sig.instrument,
        fields=SignalFields.model_validate(sig.fields),
        field_sources=sig.field_sources,  # validated into Provenance
    )


async def record_stage(db: AsyncSession, key: EventKey, attempt: int, stage: Stage) -> None:
    """Per-stage timestamp (PRD §12); idempotent per (event, attempt, stage)."""
    await db.execute(
        pg_insert(StageTiming)
        .values(
            message_id=key.message_id,
            event_type=key.event_type,
            event_seq=key.event_seq,
            attempt=attempt,
            stage=stage,
        )
        .on_conflict_do_nothing()
    )


def _event(kind: str, card_id: uuid.UUID) -> str:
    """NOTIFY payload: ids only, clients refetch (F029)."""
    return json.dumps({"type": kind, "id": str(card_id)})


async def apply_raw_review(
    db: AsyncSession,
    ctx: ProcessingContext,
    reason: str,
    *,
    mark: InboxStatus | None,
    interpreted: str | None = None,
    trace: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """FR-1e / FR-9: an `unparsed` signal + a blank review card showing the raw text.
    `mark=None` keeps the inbox status (poison: already `failed`). Returns the new card id."""
    env = ctx.envelope
    signal_id = signal_id_for(env.key, 0)
    parsed = ParsedSignal(msg_type=MsgType.UNPARSED)
    outcome = SignalOutcome(0, signal_id, parsed, None, None, None, None, [])
    inp = ApplyInput(ctx, Interpreter.NONE, {}, [outcome])
    card_id: uuid.UUID | None = None
    if await _insert_signal(db, inp, outcome):
        proposal = ProposedAction(
            action=CardAction.REVIEW, card_type=CardType.REVIEW, review_reasons=[reason]
        )
        key = f"{signal_id}:{CardAction.REVIEW}"
        card = Card(
            card_id=cards.card_id_for(key),
            dedupe_key=key,
            source=CardSource.SIGNAL,
            signal_id=signal_id,
            action=CardAction.REVIEW,
            card_type=CardType.REVIEW,
            state=CardState.PENDING,
            provider_id=env.provider_id,
            proposal=proposal.model_dump(mode="json"),
            posted_at=env.posted_at,
        )
        if await cards.insert(db, card):
            card_id = card.card_id
            await notify(db, "cards", _event("card.created", card.card_id))
    if mark is not None:
        await inbox.mark(
            db,
            env.key,
            mark,
            late=ctx.late,
            interpret_outcome=interpreted,
            interpret_reasons=trace,
        )
    return card_id
