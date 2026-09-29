"""`BrokerEventConsumer`: lifespan task reading every broker event stream (F040, OD-10).

Each event is applied in its own transaction (`apply_broker_event`); after it commits the
`on_fill` hooks run (bracket placement F041, exit progress F043, stop qty F042, reconcile F046).
No cursor is persisted: events are idempotent and startup reconciliation (F046) catches up.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.broker.port import BrokerEvent, BrokerEvents
from app.db import in_transaction
from app.execution.fills import Applied, apply_broker_event
from app.execution.models import OrderEventSource

logger = logging.getLogger(__name__)
FillHook = Callable[[Applied], Awaitable[None]]


class BrokerEventConsumer:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], sources: list[BrokerEvents]
    ) -> None:
        self._sessions = sessions
        self._sources = sources
        self.on_fill: list[FillHook] = []

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            for source in self._sources:
                group.create_task(self._consume(source))

    async def _consume(self, source: BrokerEvents) -> None:
        async for event in source.events():
            await self.handle(event)

    async def handle(
        self, event: BrokerEvent, source: OrderEventSource = OrderEventSource.BROKER_STREAM
    ) -> Applied | None:
        """One event, one transaction; a failing event is logged and never stops the stream
        (the broker stays the source of truth; reconciliation repairs)."""
        try:
            async with in_transaction(self._sessions, None) as db:
                applied = await apply_broker_event(db, event, source)
        except Exception:
            logger.exception("broker event failed", extra={"coid": event.client_order_id})
            return None
        if applied is not None:
            for hook in self.on_fill:
                try:
                    await hook(applied)
                except Exception:
                    logger.exception("on_fill hook failed", extra={"hook": repr(hook)})
        return applied
