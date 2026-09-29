"""`stops` / `stop_events` queries (F042). No decisions."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.execution.models import Stop, StopChange, StopEvent, StopStatus

ACTIVE = (StopStatus.PENDING, StopStatus.WORKING, StopStatus.SUSPENDED)


async def active(db: AsyncSession, lot_id: uuid.UUID) -> list[Stop]:
    """Locked working / suspended legs of a lot, stop first then TPs by index."""
    rows = await db.scalars(
        select(Stop)
        .where(col(Stop.lot_id) == lot_id, col(Stop.status).in_(ACTIVE))
        .order_by(col(Stop.kind).desc(), col(Stop.tp_index))
        .with_for_update()
    )
    return list(rows.all())


async def by_order(db: AsyncSession, order_id: uuid.UUID) -> Stop | None:
    stop: Stop | None = await db.scalar(
        select(Stop).where(col(Stop.order_id) == order_id).with_for_update()
    )
    return stop


async def add(db: AsyncSession, stop: Stop, change: StopChange = StopChange.CREATE) -> Stop:
    db.add(stop)
    await db.flush()
    db.add(StopEvent(stop_id=stop.stop_id, change=change, order_id=stop.order_id))
    await db.flush()
    return stop


async def set_status(
    db: AsyncSession,
    stop: Stop,
    status: StopStatus,
    change: StopChange,
    *,
    detail: dict[str, Any] | None = None,
) -> Stop:
    stop.status = status
    stop.updated_at = datetime.now(UTC)
    db.add(stop)
    db.add(
        StopEvent(stop_id=stop.stop_id, change=change, order_id=stop.order_id, detail=detail or {})
    )
    await db.flush()
    return stop
