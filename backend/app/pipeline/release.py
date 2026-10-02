"""Off-hours release (F039, PRD 7.7, C59, OD-29): at 9:30 ET the held event's cards are built.

The provider ledger was updated when the message arrived; here every signal of the held event
is validated and decided again against the CURRENT ledger (a position closed overnight gets no
entry card) with fresh quotes, and its cards are inserted by the same code as `apply`.
Validity starts at the release (OD-29, user-confirmed 2026-09-29), not at the post time.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.cards.approval_service import ApprovalService
from app.cards.models import CardAction, CardSource
from app.db import in_transaction
from app.decide.types import ProposedAction
from app.dispatcher.types import ProcessingContext
from app.inbox.models import EventType, MessageEvent
from app.inbox.repo import to_envelope_row
from app.interpret.models import LinkOutcome, Signal
from app.ledger import repo as ledger
from app.ledger.correlation import LinkDecision
from app.ledger.models import PositionStatus, ProviderPosition
from app.ledger.positions import OPENING, state_of
from app.markettime import Clock
from app.pipeline.apply import insert_card, stored_signal
from app.pipeline.plan import Planner
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.timers.models import Timer

logger = logging.getLogger(__name__)
SIZED = frozenset({CardAction.ENTRY, CardAction.ADD})


class ReleaseService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        planner: Planner,
        config: ConfigService,
        clock: Clock,
        approvals: ApprovalService | None,
    ) -> None:
        self._sessions = sessions
        self._planner = planner
        self._config = config
        self._clock = clock
        self._approvals = approvals

    async def on_release(self, timer: Timer) -> None:
        """`offhours_release` timer handler: one held message event."""
        if timer.message_id is None or timer.event_type is None or timer.event_seq is None:
            return
        now, cfg = self._clock.now(), self._config.get() or RuntimeConfig()
        async with self._sessions() as db:
            event = await db.get(
                MessageEvent, (timer.message_id, timer.event_type, timer.event_seq)
            )
            if event is None:
                return
            signals = list(
                (
                    await db.scalars(
                        select(Signal)
                        .where(
                            col(Signal.message_id) == event.message_id,
                            col(Signal.event_type) == event.event_type,
                            col(Signal.event_seq) == event.event_seq,
                        )
                        .order_by(col(Signal.idx))
                    )
                ).all()
            )
            edits = await _latest_edits(db, event)
            inputs = [(sig, *(await _link_and_position(db, sig.signal_id))) for sig in signals]
        env = to_envelope_row(event)
        ctx = ProcessingContext(
            envelope=env, attempt=1, started_at=now, age_s=0, stale=False, late=False
        )
        planned: list[tuple[Signal, uuid.UUID | None, list[ProposedAction]]] = []
        for sig, link, row in inputs:
            linked = link is not None and link.outcome == LinkOutcome.LINKED
            proposals = await self._planner.redecide(
                ctx,
                stored_signal(edits.get(sig.idx, sig) if sig.msg_type in OPENING else sig),
                link,
                row.provider_position_id if row is not None else None,
                state_of(row) if row is not None and linked else None,
                cfg,
                posted_at=now,
                now=now,
            )
            closed = row is not None and row.status == PositionStatus.CLOSED
            if closed:  # closed overnight: nothing left to enter (7.7)
                proposals = [p for p in proposals if p.action not in SIZED]
            planned.append((sig, row.provider_position_id if row else None, proposals))

        created: list[uuid.UUID] = []
        async with in_transaction(self._sessions, None) as db:
            for sig, position_id, proposals in planned:
                lot = await ledger.open_lot_for_position(db, position_id) if position_id else None
                for p in proposals:
                    card_id = await insert_card(
                        db,
                        p,
                        key=f"{sig.signal_id}:{p.action}",
                        source=CardSource.SIGNAL,
                        signal_id=sig.signal_id,
                        provider_id=sig.provider_id,
                        position_id=position_id,
                        lot_id=lot.lot_id if lot and p.action != CardAction.ENTRY else None,
                        posted_at=env.posted_at,
                    )
                    if card_id is not None:
                        created.append(card_id)
        logger.info("off-hours event released", extra={"cards": len(created)})
        if self._approvals is not None:
            await self._approvals.start_many(created)


async def _latest_edits(db: AsyncSession, event: MessageEvent) -> dict[int, Signal]:
    """The message's latest opening signal per index from its edits (PRD 7.5, C48): an edit
    while the card is held only corrected the ledger, so the card must carry the edited fields.
    Link, position and card key stay the held create signal's, as for any release."""
    rows = await db.scalars(
        select(Signal)
        .where(
            col(Signal.message_id) == event.message_id,
            col(Signal.provider_id) == event.provider_id,
            col(Signal.event_type) == EventType.EDIT,
            col(Signal.msg_type).in_(OPENING),
        )
        .order_by(col(Signal.created_at), col(Signal.event_seq), col(Signal.idx))
    )
    return {sig.idx: sig for sig in rows}  # later edits overwrite earlier ones


async def _link_and_position(
    db: AsyncSession, signal_id: uuid.UUID
) -> tuple[LinkDecision | None, ProviderPosition | None]:
    link = await ledger.latest_link(db, signal_id)
    if link is None:
        return None, None
    row = (
        await db.get(ProviderPosition, link.provider_position_id)
        if link.provider_position_id
        else None
    )
    decision = LinkDecision(
        link.outcome,
        link.method,
        link.confidence,
        link.provider_position_id,
        tuple(link.candidate_position_ids),
    )
    return decision, row
