"""AC-2 audit artifact: orders with neither an approval nor an allowed automatic reason.
The DB CHECK `approved_or_auto` keeps this empty; the query proves it per stage (PRD §17)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.execution.models import Order


async def ac2_violations(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(col(Order.order_id)).where(
            col(Order.approval_id).is_(None), col(Order.auto_reason).is_(None)
        )
    )
    return list(rows.all())
