"""Timer rows. Scheduling is idempotent on `dedupe_key`; firing is claimed by one UPDATE."""

import uuid
from datetime import datetime

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
