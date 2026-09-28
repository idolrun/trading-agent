"""Read side of orders and fills (FR-25). No writes."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.pagination import cut, keyset
from app.api.schemas.orders import FillOut, OrderDetail, OrderEventOut, OrderOut
from app.execution.models import Fill, Order, OrderEvent


def order_out(o: Order) -> OrderOut:
    return OrderOut(
        id=o.order_id,
        card_id=o.card_id,
        lot_id=o.lot_id,
        kind=o.kind,
        side=o.side,
        status=o.status,
        client_order_id=o.client_order_id,
        broker_symbol=o.broker_symbol,
        qty=o.qty,
        filled_qty=o.filled_qty,
        limit_price=o.limit_price,
        stop_price=o.stop_price,
        mode=o.mode,
        auto_reason=o.auto_reason,
        created_at=o.created_at,
    )


def fill_out(f: Fill) -> FillOut:
    return FillOut(
        id=f.fill_id, order_id=f.order_id, qty=f.qty, price=f.price, filled_at=f.filled_at
    )


async def list_orders(
    db: AsyncSession,
    *,
    status: str | None,
    lot_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[OrderOut], str | None]:
    query = select(Order)
    if status is not None:
        query = query.where(col(Order.status) == status)
    if lot_id is not None:
        query = query.where(col(Order.lot_id) == lot_id)
    rows = (
        await db.scalars(
            keyset(query, col(Order.created_at), col(Order.order_id), cursor, limit, uuid.UUID)
        )
    ).all()
    page, nxt = cut(rows, limit, lambda o: (o.created_at, o.order_id))
    return [order_out(o) for o in page], nxt


async def for_lot(db: AsyncSession, lot_id: uuid.UUID) -> list[OrderOut]:
    rows = await db.scalars(
        select(Order).where(col(Order.lot_id) == lot_id).order_by(col(Order.created_at))
    )
    return [order_out(o) for o in rows]


async def get_order(db: AsyncSession, order_id: uuid.UUID) -> OrderDetail | None:
    o = await db.get(Order, order_id)
    if o is None:
        return None
    events = await db.scalars(
        select(OrderEvent).where(col(OrderEvent.order_id) == order_id).order_by(col(OrderEvent.id))
    )
    fills = await db.scalars(
        select(Fill).where(col(Fill.order_id) == order_id).order_by(col(Fill.filled_at))
    )
    return OrderDetail(
        **order_out(o).model_dump(),
        events=[
            OrderEventOut(
                status=e.status, source=e.source, broker_payload=e.broker_payload, at=e.at
            )
            for e in events
        ],
        fills=[fill_out(f) for f in fills],
    )


async def list_fills(
    db: AsyncSession,
    *,
    order_id: uuid.UUID | None,
    since: datetime | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[FillOut], str | None]:
    query = select(Fill)
    if order_id is not None:
        query = query.where(col(Fill.order_id) == order_id)
    if since is not None:
        query = query.where(col(Fill.filled_at) >= since)
    rows = (
        await db.scalars(
            keyset(query, col(Fill.filled_at), col(Fill.fill_id), cursor, limit, uuid.UUID)
        )
    ).all()
    page, nxt = cut(rows, limit, lambda f: (f.filled_at, f.fill_id))
    return [fill_out(f) for f in page], nxt
