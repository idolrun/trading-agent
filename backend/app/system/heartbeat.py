"""Dispatcher watchdog heartbeat (TRD §2.4): written every 5 s; `/health` checks its age."""

import asyncio
import logging
import os
import socket
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.db import in_transaction
from app.markettime import Clock
from app.system.models import DispatcherHeartbeat

logger = logging.getLogger(__name__)


async def beat(db: AsyncSession, now: datetime) -> None:
    values = {"beat_at": now, "host": socket.gethostname(), "pid": os.getpid()}
    await db.execute(
        insert(DispatcherHeartbeat)
        .values(id=1, **values)
        .on_conflict_do_update(index_elements=["id"], set_=values)
    )


async def last_beat(db: AsyncSession) -> datetime | None:
    beat_at: datetime | None = await db.scalar(select(col(DispatcherHeartbeat.beat_at)))
    return beat_at


async def run_heartbeat(
    session_factory: async_sessionmaker[AsyncSession], clock: Clock, interval_s: float = 5.0
) -> None:
    """Background task. A failed beat is logged and retried; a stale beat fails `/health`."""
    while True:
        try:
            async with in_transaction(session_factory, None) as db:
                await beat(db, clock.now())
        except Exception:
            logger.exception("heartbeat write failed")
        await asyncio.sleep(interval_s)
