"""Corrections, edits and deletes (F038, PRD 7.5, C16, C48, C78, AC-5, OD-24).

Planning (before the apply transaction): an edit event, or a `correction` signal, is turned
into a correction of the provider position the original message opened. The corrected
interpretation is planned as a fresh entry (book without that position) so it sizes and
validates like a new card; its contract fields become a `correct` ledger event. The apply
transaction then decides by the target's state (`_apply_correction`).

Numbers come only from the provider's own text (literal spans): the system never edits one.
"""

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.cards import repo as cards
from app.cards.models import CardAction, CardActor, CardReason, CardState, CardType
from app.decide.risk_snapshot import lot_contract
from app.decide.types import ProposedAction
from app.dispatcher.types import ProcessingContext
from app.inbox.envelope import Envelope
from app.inbox.models import EventType, MessageEvent
from app.interpret.models import LinkMethod, LinkOutcome, MsgType, Signal
from app.interpret.signal import ParsedSignal, ParsedSignals, SignalFields
from app.ledger import repo as ledger
from app.ledger.correlation import LinkDecision, ParentLink
from app.ledger.lots import OPTION_MULTIPLIER
from app.ledger.models import Instrument, ProviderPosition
from app.ledger.positions import OPENING, PositionState, PositionUpdate, apply_correction
from app.marketdata.port import MarketData
from app.pipeline.apply import Correction, SignalOutcome, raise_alert
from app.pipeline.plan import Book, Planner
from app.runtime_config.schema import RuntimeConfig
from app.validate.normalize import mid_price

logger = logging.getLogger(__name__)
CONTRACT_FIELDS = (
    "underlying",
    "expiry_token",
    "strike",
    "right",
    "entry_price",
    "entry_price_low",
    "entry_price_high",
)
PRICE_FIELDS = ("entry_price", "entry_price_low", "entry_price_high")
CORRECTED_STATE = (
    "instrument",
    "underlying",
    "expiry",
    "strike",
    "option_right",
    "provider_entry_price",
)


@dataclass(frozen=True)
class Target:
    """The corrected message: its signals, the position it opened and its opening signal."""

    signal_ids: tuple[uuid.UUID, ...]
    position_id: uuid.UUID | None
    opening: ParsedSignal | None
    text: str | None


class CorrectionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        planner: Planner,
        marketdata: MarketData,
    ) -> None:
        self._sessions = sessions
        self._planner = planner
        self._md = marketdata

    @staticmethod
    def applies(env: Envelope, parsed: ParsedSignals) -> bool:
        return env.key.event_type == EventType.EDIT or any(
            s.msg_type == MsgType.CORRECTION for s in parsed.signals
        )

    async def plan(
        self,
        ctx: ProcessingContext,
        parsed: ParsedSignals,
        config: RuntimeConfig,
        book: Book,
        parents: tuple[ParentLink, ...],
        now: datetime,
    ) -> list[SignalOutcome]:
        env = ctx.envelope
        # plan() mutates its book: keep ours as it is in the DB for the correction re-plans
        outcomes = await self._planner.plan(ctx, parsed, config, dict(book), parents, now)
        if env.key.event_type == EventType.EDIT:
            async with self._sessions() as db:
                target = await _message_target(db, env.provider_id, env.key.message_id, env)
            if target is None:
                return outcomes  # nothing came of the original: plain new message
            return [await self._edited(ctx, o, target, config, book, now) for o in outcomes]
        out = []
        for o in outcomes:
            if o.parsed.msg_type == MsgType.CORRECTION:
                o = await self._corrected(ctx, o, config, book, now)
            out.append(o)
        return out

    async def _edited(
        self,
        ctx: ProcessingContext,
        o: SignalOutcome,
        t: Target,
        config: RuntimeConfig,
        book: Book,
        now: datetime,
    ) -> SignalOutcome:
        if t.position_id is not None and o.parsed.msg_type in OPENING:
            return await self._as_correction(ctx, o, o.parsed, t, config, book, now)
        # an edited follow-up: the ledger keeps the original effect (never re-applied); any
        # card becomes a review card for fresh approval
        return replace(
            o,
            update=None,
            cards=[
                c.model_copy(
                    update={
                        "card_type": CardType.REVIEW,
                        "review_reasons": [*c.review_reasons, "edited_message"],
                    }
                )
                for c in o.cards
            ],
            correction=Correction(t.signal_ids, None, t.text, None),
        )

    async def _corrected(
        self,
        ctx: ProcessingContext,
        o: SignalOutcome,
        config: RuntimeConfig,
        book: Book,
        now: datetime,
    ) -> SignalOutcome:
        env = ctx.envelope
        target_message = o.parsed.fields.correction_target_message_id
        async with self._sessions() as db:
            t: Target | None = None
            if target_message is not None:
                t = await _message_target(db, env.provider_id, target_message, None)
            elif o.link is not None and o.link.outcome == LinkOutcome.LINKED and o.position_id:
                t = await _position_target(db, o.position_id)
        if t is None or t.position_id is None or t.opening is None:
            return replace(  # target not this provider's opening message: the user decides
                o,
                update=None,
                cards=[
                    ProposedAction(
                        action=CardAction.REVIEW,
                        card_type=CardType.REVIEW,
                        review_reasons=["correction_target_unknown"],
                    )
                ],
            )
        stated = {
            name: value
            for name in CONTRACT_FIELDS
            if (value := getattr(o.parsed.fields, name)) is not None
        }
        # a price stated for the old contract says nothing about the corrected one
        dropped = {name: None for name in PRICE_FIELDS if name not in stated}
        merged = t.opening.model_copy(
            update={"fields": t.opening.fields.model_copy(update=dropped | stated)}
        )
        return await self._as_correction(ctx, o, merged, t, config, book, now)

    async def _as_correction(
        self,
        ctx: ProcessingContext,
        o: SignalOutcome,
        corrected: ParsedSignal,
        t: Target,
        config: RuntimeConfig,
        book: Book,
        now: datetime,
    ) -> SignalOutcome:
        assert t.position_id is not None  # noqa: S101 — callers check
        position = book.get(t.position_id)
        without = {k: v for k, v in book.items() if k != t.position_id}
        [fresh] = await self._planner.plan(
            ctx, ParsedSignals(signals=[corrected], interpreter="llm"), config, without, (), now
        )
        update = None
        if position is not None and fresh.update is not None:
            update = _correct(position, fresh.update.after)
        return SignalOutcome(
            idx=o.idx,
            signal_id=o.signal_id,
            parsed=o.parsed,
            link=LinkDecision(LinkOutcome.LINKED, LinkMethod.CODE, Decimal(1), t.position_id),
            position_id=t.position_id if position is not None else None,
            update=update,
            lot_id=None,
            cards=fresh.cards,
            correction=Correction(t.signal_ids, t.position_id, t.text, await self._pnl(t)),
        )

    async def _pnl(self, t: Target) -> Decimal | None:
        """Live P&L of the user's open lot on the corrected position (alert content)."""
        if t.position_id is None:
            return None
        async with self._sessions() as db:
            lot = await ledger.open_lot_for_position(db, t.position_id)
        if lot is None or lot.avg_price is None:
            return None
        try:
            mid = mid_price(await self._md.quote(lot_contract(lot)))
        except Exception:
            return None
        if mid is None:
            return None
        multiplier = OPTION_MULTIPLIER if lot.instrument == Instrument.OPTION else 1
        return (mid - lot.avg_price) * lot.qty * multiplier

    async def handle_delete(self, db: AsyncSession, env: Envelope) -> list[uuid.UUID]:
        """7.5: the original message deleted. Pending card -> invalidated + alert; after the
        trade -> alert only. The provider ledger is left as is (PRD does not say to close it).
        Returns the invalidated card ids (their approval graphs resume)."""
        t = await _message_target(db, env.provider_id, env.key.message_id, env)
        if t is None:
            return []
        targets = await cards.of_signals(db, t.signal_ids)
        pending = [c for c in targets if c.state == CardState.PENDING]
        traded = any(c.state in (CardState.APPROVED, CardState.SUBMITTED) for c in targets) or (
            t.position_id is not None and await ledger.has_lot(db, t.position_id)
        )
        for card in pending:
            await cards.transition(
                db, card, CardState.INVALIDATED, CardReason.DELETED, CardActor.SYSTEM
            )
        if pending or traded:
            await raise_alert(
                db,
                AlertKind.MESSAGE_DELETED,
                Severity.WARNING,
                "Provider deleted a message: its pending card was invalidated. The provider "
                "position is left open."
                if pending
                else "Provider deleted a message after you traded. No action proposed.",
                details={"original_text": t.text, "position_id": str(t.position_id)},
                dedupe_key=f"message_deleted:{env.key.message_id}",
                message_id=env.key.message_id,
            )
        return [c.card_id for c in pending]


def _correct(position: PositionState, fresh: PositionState) -> PositionUpdate | None:
    changes = {
        name: getattr(fresh, name)
        for name in CORRECTED_STATE
        if getattr(fresh, name) is not None and getattr(fresh, name) != getattr(position, name)
    }
    return apply_correction(position, changes) if changes else None


async def _message_target(
    db: AsyncSession, provider_id: int, message_id: int, exclude: Envelope | None
) -> Target | None:
    """The same provider's earlier signals of `message_id` (an edit excludes itself)."""
    rows = list(
        (
            await db.scalars(
                select(Signal)
                .where(col(Signal.message_id) == message_id, col(Signal.provider_id) == provider_id)
                .order_by(col(Signal.created_at), col(Signal.idx))
            )
        ).all()
    )
    if exclude is not None:
        k = exclude.key
        rows = [s for s in rows if (s.event_type, s.event_seq) != (k.event_type, k.event_seq)]
    if not rows:
        return None
    ids = tuple(s.signal_id for s in rows)
    position = await db.scalar(
        select(ProviderPosition)
        .where(col(ProviderPosition.opened_by_signal_id).in_(list(ids)))
        .limit(1)
    )
    opening = next(
        (s for s in rows if position and s.signal_id == position.opened_by_signal_id), None
    )
    event = await db.scalar(
        select(MessageEvent)
        .where(col(MessageEvent.message_id) == message_id)
        .order_by(col(MessageEvent.posted_at))
        .limit(1)
    )
    return Target(
        signal_ids=ids,
        position_id=position.provider_position_id if position else None,
        opening=_parsed(opening) if opening else None,
        text=event.text if event else None,
    )


async def _position_target(db: AsyncSession, position_id: uuid.UUID) -> Target | None:
    position = await db.get(ProviderPosition, position_id)
    if position is None or position.opened_by_signal_id is None:
        return None
    sig = await db.get(Signal, position.opened_by_signal_id)
    if sig is None:
        return None
    return await _message_target(db, sig.provider_id, sig.message_id, None)


def _parsed(sig: Signal) -> ParsedSignal:
    return ParsedSignal(
        msg_type=sig.msg_type,
        instrument=sig.instrument,
        fields=SignalFields.model_validate(sig.fields),
    )
