"""Timer rows. Scheduling is idempotent on `dedupe_key`; firing is claimed by one UPDATE."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.db import notify
from app.timers.models import Timer, TimerKind, TimerStatus


async def schedule(
    db: AsyncSession,
    kind: TimerKind,
    due_at: datetime,
    dedupe_key: str,
    *,
    card_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
) -> bool:
    """False = a timer with this key already exists (re-run). Wakes the scheduler on commit."""
    inserted = await db.scalar(
        pg_insert(Timer)
        .values(
            timer_id=uuid.uuid4(),
            kind=kind,
            due_at=due_at,
            dedupe_key=dedupe_key,
            card_id=card_id,
            lot_id=lot_id,
            order_id=order_id,
            status=TimerStatus.SCHEDULED,
        )
        .on_conflict_do_nothing()
        .returning(col(Timer.timer_id))
    )
    if inserted is not None:
        await notify(db, "timers", kind)
    return inserted is not None


async def claim_due(db: AsyncSession, now: datetime, kinds: list[TimerKind]) -> Timer | None:
    """Mark the earliest due timer of these kinds `fired` and return it. Exactly once: the row
    is locked (`SKIP LOCKED`) and re-checked `scheduled`, so racing schedulers never share."""
    due = (
        select(col(Timer.timer_id))
        .where(
            col(Timer.status) == TimerStatus.SCHEDULED,
            col(Timer.due_at) <= now,
            col(Timer.kind).in_(kinds),
        )
        .order_by(col(Timer.due_at), col(Timer.timer_id))
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    timer: Timer | None = await db.scalar(
        update(Timer)
        .where(col(Timer.timer_id) == due, col(Timer.status) == TimerStatus.SCHEDULED)
        .values(status=TimerStatus.FIRED, fired_at=now)
        .returning(Timer)
    )
    return timer


async def next_due(db: AsyncSession, kinds: list[TimerKind]) -> datetime | None:
    due: datetime | None = await db.scalar(
        select(func.min(col(Timer.due_at))).where(
            col(Timer.status) == TimerStatus.SCHEDULED, col(Timer.kind).in_(kinds)
        )
    )
    return due


async def cancel(db: AsyncSession, dedupe_key: str) -> bool:
    cancelled = await db.scalar(
        update(Timer)
        .where(col(Timer.dedupe_key) == dedupe_key, col(Timer.status) == TimerStatus.SCHEDULED)
        .values(status=TimerStatus.CANCELLED)
        .returning(col(Timer.timer_id))
    )
    return cancelled is not None
