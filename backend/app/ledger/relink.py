"""Out-of-order arrival (F037, PRD FR-1b / C89 / AC-18, TRD §2.4).

A late event is applied normally (labelled `late`). Afterwards this provider's later
follow-ups that were linked to nothing (or ambiguously) are re-interpreted against today's
context; a follow-up that now links to a position becomes a system review card proposing the
link. Nothing changes without the user's approval: approving writes a `user_correction` link,
applies the provider-ledger change, and runs the normal decide/card flow for the user's lot.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

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
from app.db import in_transaction
from app.decide.types import ProposedAction
from app.dispatcher.types import ProcessingContext
from app.inbox.models import MessageEvent
from app.inbox.repo import to_envelope_row
from app.interpret import context_repo
from app.interpret.models import LinkMethod, LinkOutcome, MessageLink, Signal
from app.interpret.signal import ParsedSignals
from app.ledger import repo as ledger
from app.ledger.correlation import FOLLOW_UPS, ParentLink
from app.ledger.positions import state_of
from app.markettime import Clock
from app.pipeline.apply import BookChanged, SignalOutcome, insert_card, stored_signal
from app.pipeline.plan import Planner
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService

logger = logging.getLogger(__name__)
Recheck = Callable[[ProcessingContext], Awaitable[ParsedSignals | None]]


class RelinkFailed(Exception):
    """The proposed link no longer holds (e.g. the position closed meanwhile)."""


class RelinkService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        planner: Planner,
        config: ConfigService,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._planner = planner
        self._config = config
        self._clock = clock
        self.recheck: Recheck | None = None  # the message graph in recheck mode (F035)

    async def recheck_after_late(
        self, provider_id: int, late_posted_at: datetime
    ) -> list[uuid.UUID]:
        """New re-link review card ids. Unchanged results create nothing."""
        if self.recheck is None:
            return []
        async with self._sessions() as db:
            rows = await ledger.unlinked_follow_ups_after(
                db, provider_id, late_posted_at, FOLLOW_UPS
            )
        by_event: dict[tuple[int, str, int], tuple[MessageEvent, list[Signal]]] = {}
        for sig, event in rows:
            by_event.setdefault((event.message_id, event.event_type, event.event_seq), (event, []))[
                1
            ].append(sig)
        created: list[uuid.UUID] = []
        for event, signals in by_event.values():
            ctx = self._ctx(event)
            parsed = await self.recheck(ctx)
            if parsed is None:
                continue
            outcomes = {o.idx: o for o in await self._plan(ctx, parsed, parents=None)}
            for sig in signals:
                o = outcomes.get(sig.idx)
                if o is None or o.link is None or o.link.outcome != LinkOutcome.LINKED:
                    continue
                assert o.position_id is not None  # noqa: S101 — linked has a position
                card_id = await self._propose(sig, o.position_id, event)
                if card_id is not None:
                    created.append(card_id)
        return created

    async def apply_approved(
        self, card: Card, signal_id: uuid.UUID
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        """The user approved a re-link card: (new card ids, superseded card ids)."""
        position_id = card.provider_position_id
        assert position_id is not None and card.provider_id is not None  # noqa: S101
        async with self._sessions() as db:
            sig = await db.get(Signal, signal_id)
            assert sig is not None  # noqa: S101 — the proposal came from it
            event = await _event(db, sig)
        ctx = self._ctx(event)
        parsed = ParsedSignals(
            signals=[stored_signal(sig)],
            interpreter="llm",
        )
        # the user's choice acts like a reply to the position's message: one parent -> linked
        [o] = await self._plan(ctx, parsed, parents=(ParentLink(card.provider_id, position_id),))
        if o.link is None or o.link.outcome != LinkOutcome.LINKED or o.position_id != position_id:
            raise RelinkFailed("the position is no longer open")
        # C22 for the resulting cards starts now: the user just made them possible
        shift = self._clock.now() - event.posted_at
        proposals = [
            p.model_copy(update={"valid_until": p.valid_until + shift}) if p.valid_until else p
            for p in o.cards
        ]
        async with in_transaction(self._sessions, None) as db:
            return await self._write(db, card, signal_id, o, proposals)

    async def _write(
        self,
        db: AsyncSession,
        card: Card,
        signal_id: uuid.UUID,
        o: SignalOutcome,
        proposals: list[ProposedAction],
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        provider_id, position_id = card.provider_id, card.provider_position_id
        assert provider_id is not None and position_id is not None  # noqa: S101
        rows = {p.provider_position_id: p for p in await ledger.lock_open_book(db, provider_id)}
        if position_id not in rows:
            raise BookChanged("position closed before the re-link was applied")
        latest = await ledger.latest_link(db, signal_id)
        db.add(
            MessageLink(
                signal_id=signal_id,
                provider_id=provider_id,
                provider_position_id=position_id,
                outcome=LinkOutcome.LINKED,
                method=LinkMethod.USER_CORRECTION,
                confidence=None,
                candidate_position_ids=[],
                supersedes_link_id=latest.id if latest else None,
            )
        )
        if o.update is not None:
            await ledger.record(
                db,
                provider_id=provider_id,
                row=rows[position_id],
                update=o.update,
                signal_id=signal_id,
                position_id=position_id,
            )
        superseded = await self._supersede(db, signal_id, position_id, o)
        created = []
        for p in proposals:
            new_id = await insert_card(
                db,
                p,
                key=f"relink:{signal_id}:{p.action}",
                source=CardSource.SYSTEM,
                signal_id=None,  # UNIQUE(signal_id, action): the original card keeps that slot
                provider_id=provider_id,
                position_id=position_id,
                lot_id=o.lot_id,
                posted_at=self._clock.now(),
            )
            if new_id is not None:
                created.append(new_id)
        return created, superseded

    async def _supersede(
        self, db: AsyncSession, signal_id: uuid.UUID, position_id: uuid.UUID, o: SignalOutcome
    ) -> list[uuid.UUID]:
        """The follow-up's own unlinked review card, and (OD-14) pending entries on P."""
        stale = list(
            (
                await db.scalars(
                    select(Card)
                    .where(col(Card.signal_id) == signal_id, col(Card.state) == CardState.PENDING)
                    .with_for_update()
                )
            ).all()
        )
        if supersedes(o.parsed.msg_type, CardAction.ENTRY):
            stale += await cards.pending_on_position(db, position_id, ENTRY_ACTIONS)
        for c in stale:
            await cards.transition(
                db,
                c,
                CardState.INVALIDATED,
                CardReason.SUPERSEDED,
                CardActor.USER,
                detail={"relinked_signal_id": str(signal_id)},
            )
        return [c.card_id for c in stale]

    async def _propose(
        self, sig: Signal, position_id: uuid.UUID, event: MessageEvent
    ) -> uuid.UUID | None:
        proposal = ProposedAction(
            action=CardAction.REVIEW,
            card_type=CardType.REVIEW,
            review_reasons=["relink"],
            candidate_position_ids=[position_id],
            relink_signal_id=sig.signal_id,
        )
        async with in_transaction(self._sessions, None) as db:
            lot = await ledger.open_lot_for_position(db, position_id)
            return await insert_card(
                db,
                proposal,
                key=f"relink:{sig.signal_id}:{position_id}",
                source=CardSource.SYSTEM,
                signal_id=None,
                provider_id=sig.provider_id,
                position_id=position_id,
                lot_id=lot.lot_id if lot else None,
                posted_at=event.posted_at,
            )

    async def _plan(
        self,
        ctx: ProcessingContext,
        parsed: ParsedSignals,
        *,
        parents: tuple[ParentLink, ...] | None,
    ) -> list[SignalOutcome]:
        env = ctx.envelope
        async with self._sessions() as db:
            rows = await context_repo.open_book(db, env.provider_id)
            if parents is None:
                parents = await ledger.parent_links(db, env.reply_to_id) if env.reply_to_id else ()
        book = {r.provider_position_id: state_of(r) for r in rows}
        cfg = self._config.get() or RuntimeConfig()
        return await self._planner.plan(ctx, parsed, cfg, book, parents, self._clock.now())

    def _ctx(self, event: MessageEvent) -> ProcessingContext:
        now = self._clock.now()
        return ProcessingContext(
            envelope=to_envelope_row(event),
            attempt=1,
            started_at=now,
            age_s=(now - event.posted_at).total_seconds(),
            stale=False,
            late=True,
        )


async def _event(db: AsyncSession, sig: Signal) -> MessageEvent:
    event = await db.get(MessageEvent, (sig.message_id, sig.event_type, sig.event_seq))
    assert event is not None  # noqa: S101 — FK
    return event
