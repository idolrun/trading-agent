"""Expiration management (F045, PRD 7.6, C19, FR-24, UC7, AC-6, §9).

An option lot expiring today gets two timers: at cutoff 1 an urgent system `close` card (the
normal approval path); at cutoff 2, if still open and no approved exit is working, the
pre-approved end-of-day forced close (C25 exit, protective orders cancelled first). Cutoffs are
minutes before the session close (OD-21), so half days shift them.
"""

import logging
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.cards import repo as cards
from app.cards.approval_service import ApprovalService
from app.cards.models import (
    Card,
    CardAction,
    CardActor,
    CardReason,
    CardSource,
    CardState,
    CardType,
)
from app.db import in_transaction
from app.decide.types import ProposedAction
from app.execution import repo
from app.execution.exit import ExitPolicy
from app.execution.fills import Applied
from app.execution.intent import Auto
from app.execution.models import AutoReason, OrderKind
from app.ledger.models import Instrument, Lot, LotStatus
from app.markettime import Clock, session_for, to_et
from app.pipeline.apply import insert_card
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.timers import repo as timers
from app.timers.models import Timer, TimerKind

logger = logging.getLogger(__name__)


def cutoffs(day: date, cutoff_1_min: int, cutoff_2_min: int) -> tuple[datetime, datetime] | None:
    """(cutoff 1, cutoff 2) for `day`'s session, None if the exchange is closed."""
    session = session_for(day)
    if session is None:
        return None
    return (
        session.close - timedelta(minutes=cutoff_1_min),
        session.close - timedelta(minutes=cutoff_2_min),
    )


class ExpirationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        config: ConfigService,
        clock: Clock,
        approvals: ApprovalService,
        exits: ExitPolicy,
        alerts: AlertService,
    ) -> None:
        self._sessions = sessions
        self._config = config
        self._clock = clock
        self._approvals = approvals
        self._exits = exits
        self._alerts = alerts

    async def on_fill(self, applied: Applied) -> None:
        """Consumer hook: a lot opened / grown today that expires today gets its cutoffs."""
        if applied.lot is not None and applied.order.kind == OrderKind.ENTRY:
            await self.schedule_for_lot(applied.lot)

    async def schedule_today(self) -> int:
        """Startup (F046) and session open: every open lot expiring today."""
        today = to_et(self._clock.now()).date()
        async with self._sessions() as db:
            lots = (
                await db.scalars(
                    select(Lot).where(col(Lot.status) == LotStatus.OPEN, col(Lot.expiry) == today)
                )
            ).all()
        for lot in lots:
            await self.schedule_for_lot(lot)
        return len(lots)

    async def schedule_for_lot(self, lot: Lot) -> None:
        today = to_et(self._clock.now()).date()
        if lot.is_foreign or lot.instrument != Instrument.OPTION or lot.expiry != today:
            return  # foreign lots are read-only (C47)
        cfg = self._config.get() or RuntimeConfig()
        c1, c2 = cfg.cutoff_1_min_before_close, cfg.cutoff_2_min_before_close
        times = cutoffs(today, c1, c2) if c1 and c2 else None
        if times is None:
            logger.error("no cutoffs for an expiring lot", extra={"lot_id": str(lot.lot_id)})
            return  # config incomplete means no entries (missing_for_entries): cannot happen
        async with in_transaction(self._sessions, None) as db:
            for kind, due in zip((TimerKind.CUTOFF_1, TimerKind.CUTOFF_2), times, strict=True):
                await timers.schedule(
                    db, kind, due, f"{kind}:{lot.lot_id}:{today}", lot_id=lot.lot_id
                )

    async def on_cutoff_1(self, timer: Timer) -> None:
        """Urgent close card for the whole lot, approved like any card (7.6)."""
        lot = await self._open_lot(timer)
        if lot is None:
            return
        proposal = ProposedAction(
            action=CardAction.CLOSE, card_type=CardType.NORMAL, qty=lot.qty, labels=["urgent"]
        )
        async with in_transaction(self._sessions, None) as db:
            card_id = await insert_card(
                db,
                proposal,
                key=_card_key(lot.lot_id, timer),
                source=CardSource.SYSTEM,
                signal_id=None,
                provider_id=lot.provider_id,
                position_id=lot.provider_position_id,
                lot_id=lot.lot_id,
                posted_at=self._clock.now(),
            )
        if card_id is not None:
            await self._approvals.start_many([card_id])

    async def on_cutoff_2(self, timer: Timer) -> None:
        """Pre-approved forced close unless an approved exit is already working (§9)."""
        lot = await self._open_lot(timer)
        if lot is None:
            return
        async with self._sessions() as db:
            working = await repo.open_orders(db, lot.lot_id, OrderKind.EXIT)
        if working:
            return  # the user's approved exit is on it
        superseded = await self._supersede_close_card(lot.lot_id, timer)
        await self._approvals.invalidated(superseded, None)
        day = to_et(timer.due_at).date()
        await self._alerts.raise_(
            AlertKind.EOD_FORCED_CLOSE,
            Severity.URGENT,
            f"Cutoff 2: closing {lot.qty} of {lot.broker_symbol} expiring today",
            details={"qty": lot.qty, "broker_symbol": lot.broker_symbol},
            dedupe_key=f"eod_forced_close:{lot.lot_id}:{day}",
            lot_id=lot.lot_id,
        )
        await self._exits.execute(
            lot.lot_id, lot.qty, Auto(AutoReason.EOD_FORCED_CLOSE, ref=str(day))
        )

    async def _supersede_close_card(self, lot_id: uuid.UUID, timer: Timer) -> list[uuid.UUID]:
        async with in_transaction(self._sessions, None) as db:
            card = await db.scalar(
                select(Card)
                .where(col(Card.dedupe_key) == _card_key(lot_id, timer))
                .with_for_update()
            )
            if card is None or card.state != CardState.PENDING:
                return []
            await cards.transition(
                db,
                card,
                CardState.INVALIDATED,
                CardReason.SUPERSEDED,
                CardActor.TIMER,
                detail={"by": "cutoff_2"},
            )
            return [card.card_id]

    async def _open_lot(self, timer: Timer) -> Lot | None:
        if timer.lot_id is None:
            return None
        async with self._sessions() as db:
            lot = await db.get(Lot, timer.lot_id)
        if lot is None or lot.status != LotStatus.OPEN or lot.qty == 0:
            return None
        return lot


def _card_key(lot_id: uuid.UUID, timer: Timer) -> str:
    return f"cutoff_close:{lot_id}:{to_et(timer.due_at).date()}"
