"""Durable deadlines (7.2, C22, 7.6, 7.7): schedule, cancel, and a loop that fires each timer
exactly once, also after restarts. Kind-specific logic lives in the registered handlers, which
must be idempotent (e.g. expiring an already-approved card is an IllegalTransition, ignored).
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction, listen_forever
from app.markettime import Clock
from app.timers import repo
from app.timers.models import Timer, TimerKind

logger = logging.getLogger(__name__)
Handler = Callable[[Timer], Awaitable[None]]
RETRY_DELAY = timedelta(seconds=5)
MAX_SLEEP_S = 1.0


class TimerService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        alerts: AlertService,
        clock: Clock,
        listen_dsn: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._alerts = alerts
        self._clock = clock
        self._listen_dsn = listen_dsn
        self._handlers: dict[TimerKind, Handler] = {}
        self._wake = asyncio.Event()

    def register(self, kind: TimerKind, handler: Handler) -> None:
        self._handlers[kind] = handler
        self._wake.set()

    async def schedule(
        self,
        kind: TimerKind,
        due_at: datetime,
        dedupe_key: str,
        *,
        db: AsyncSession | None = None,
        card_id: uuid.UUID | None = None,
        lot_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
    ) -> bool:
        async with in_transaction(self._sessions, db) as session:
            return await repo.schedule(
                session, kind, due_at, dedupe_key, card_id=card_id, lot_id=lot_id, order_id=order_id
            )

    async def cancel(self, dedupe_key: str, *, db: AsyncSession | None = None) -> bool:
        async with in_transaction(self._sessions, db) as session:
            return await repo.cancel(session, dedupe_key)

    async def run_due(self) -> int:
        """Fire every due timer, earliest first (startup: overdue ones fire immediately)."""
        fired = 0
        while self._handlers:
            async with in_transaction(self._sessions, None) as db:
                timer = await repo.claim_due(db, self._clock.now(), list(self._handlers))
            if timer is None:
                return fired
            fired += 1
            await self._fire(timer)  # after the claim committed
        return fired

    async def run(self) -> None:
        """Lifespan task: sleep until the next due time (cap 1 s) or a NOTIFY."""
        listener = None
        if self._listen_dsn is not None:
            listener = asyncio.create_task(
                listen_forever(self._listen_dsn, "timers", self._on_notify, self._on_connect),
                name="timers-listener",
            )
        try:
            while True:
                self._wake.clear()
                await self.run_due()
                await self._sleep()
        finally:
            if listener is not None:
                listener.cancel()

    async def _sleep(self) -> None:
        delay = MAX_SLEEP_S
        if self._handlers:
            async with self._sessions() as db:
                due = await repo.next_due(db, list(self._handlers))
            if due is not None:
                delay = max(0.0, min(delay, (due - self._clock.now()).total_seconds()))
        try:
            await asyncio.wait_for(self._wake.wait(), delay)
        except TimeoutError:
            pass

    async def _on_notify(self, _payload: str) -> None:
        self._wake.set()

    async def _on_connect(self) -> None:
        self._wake.set()

    async def _fire(self, timer: Timer) -> None:
        try:
            await self._handlers[timer.kind](timer)
        except Exception as exc:
            logger.exception("timer handler failed", extra={"timer_id": str(timer.timer_id)})
            retry = not timer.dedupe_key.endswith(":retry")
            if retry:
                await self.schedule(
                    timer.kind,
                    self._clock.now() + RETRY_DELAY,
                    f"{timer.dedupe_key}:retry",
                    card_id=timer.card_id,
                    lot_id=timer.lot_id,
                    order_id=timer.order_id,
                )
            await self._alerts.raise_(
                AlertKind.TIMER_FAILED,
                Severity.WARNING,
                f"Timer {timer.kind} failed{' (retry in 5 s)' if retry else ''}: {exc}",
                dedupe_key=f"timer:{timer.timer_id}",
                card_id=timer.card_id,
            )
