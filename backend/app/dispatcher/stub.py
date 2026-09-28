"""Stand-in processor for tests and the M2 demo: records calls and marks events done/stale.
Replaced by the pipeline processor (F026) and the message graph (F035)."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import in_transaction
from app.dispatcher.types import ProcessingContext
from app.inbox import repo as inbox
from app.inbox.envelope import EventKey
from app.inbox.models import InboxStatus


@dataclass(frozen=True)
class Call:
    key: EventKey
    provider_id: int
    started: float  # loop time
    ended: float


class StubProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        delay_s: float = 0.0,
        fail: Callable[[ProcessingContext], bool] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._delay_s = delay_s
        self._fail = fail
        self.calls: list[Call] = []

    async def process(self, ctx: ProcessingContext) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        if self._fail is not None and self._fail(ctx):
            raise RuntimeError("stub processor: scripted failure")
        await asyncio.sleep(self._delay_s)
        status = InboxStatus.STALE if ctx.stale else InboxStatus.DONE
        async with in_transaction(self._sessions, None) as db:
            await inbox.mark(db, ctx.envelope.key, status, late=ctx.late)
        self.calls.append(Call(ctx.envelope.key, ctx.envelope.provider_id, started, loop.time()))
