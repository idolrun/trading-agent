"""Order and order-event queries. No decisions."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.execution.invariants import PROTECTIVE, WorkingSells
from app.execution.models import (
    OPEN_ORDER_STATUSES,
    Fill,
    Mode,
    Order,
    OrderEvent,
    OrderEventSource,
    OrderKind,
    OrderStatus,
    Side,
)


async def by_client_id(db: AsyncSession, client_order_id: str) -> Order | None:
    order: Order | None = await db.scalar(
        select(Order).where(col(Order.client_order_id) == client_order_id)
    )
    return order


async def insert_intent(db: AsyncSession, values: dict[str, Any]) -> Order | None:
    """Insert `status='intent'` + its `submit` event. None = the client id exists (re-run)."""
    order_id = await db.scalar(
        pg_insert(Order).values(**values).on_conflict_do_nothing().returning(col(Order.order_id))
    )
    if order_id is None:
        return None
    db.add(OrderEvent(order_id=order_id, status=OrderStatus.INTENT, source=OrderEventSource.SUBMIT))
    await db.flush()
    return await db.get(Order, order_id)


async def working_sells(db: AsyncSession, lot_id: uuid.UUID) -> WorkingSells:
    remaining = func.coalesce(func.sum(col(Order.qty) - col(Order.filled_qty)), 0)
    rows = await db.execute(
        select(col(Order.kind), col(Order.oco_group_id), remaining)
        .where(
            col(Order.lot_id) == lot_id,
            col(Order.side) == Side.SELL,
            col(Order.status).in_(OPEN_ORDER_STATUSES),
        )
        .group_by(col(Order.kind), col(Order.oco_group_id))
    )
    exits = ungrouped = 0
    groups: dict[uuid.UUID, tuple[int, int]] = {}
    for kind, group, qty in rows.all():
        if kind not in PROTECTIVE:
            exits += int(qty)
        elif group is None:
            ungrouped += int(qty)
        else:
            stop, tp = groups.get(group, (0, 0))
            groups[group] = (
                (stop + int(qty), tp) if kind == OrderKind.STOP else (stop, tp + int(qty))
            )
    return WorkingSells(exits=exits, protective=ungrouped, groups=groups)


async def set_status(
    db: AsyncSession,
    order: Order,
    status: OrderStatus,
    source: OrderEventSource,
    *,
    broker_order_id: str | None = None,
    filled_qty: int | None = None,
    payload: dict[str, Any] | None = None,
) -> Order:
    order.status = status
    order.updated_at = datetime.now(UTC)
    if broker_order_id is not None:
        order.broker_order_id = broker_order_id
    if filled_qty is not None:
        order.filled_qty = filled_qty
    db.add(order)
    db.add(
        OrderEvent(order_id=order.order_id, status=status, source=source, broker_payload=payload)
    )
    await db.flush()
    return order


async def open_orders(db: AsyncSession, lot_id: uuid.UUID, kind: OrderKind) -> list[Order]:
    rows = await db.scalars(
        select(Order).where(
            col(Order.lot_id) == lot_id,
            col(Order.kind) == kind,
            col(Order.status).in_(OPEN_ORDER_STATUSES),
        )
    )
    return list(rows.all())


async def orders_to_sync(db: AsyncSession) -> list[tuple[Order, int]]:
    """(order, qty of its stored fills) for reconciliation (F046): every open order sent to a
    real broker, plus any whose fill never reached the fills table (shadow orders never)."""
    applied = (
        select(func.coalesce(func.sum(col(Fill.qty)), 0))
        .where(col(Fill.order_id) == col(Order.order_id))
        .scalar_subquery()
    )
    rows = await db.execute(
        select(Order, applied).where(
            col(Order.mode) != Mode.SHADOW,
            or_(col(Order.status).in_(OPEN_ORDER_STATUSES), col(Order.filled_qty) > applied),
        )
    )
    return [(order, int(qty)) for order, qty in rows.tuples().all()]


async def open_exit_roots(db: AsyncSession) -> list[uuid.UUID]:
    """Working exit orders whose re-price loop restart must adopt (F043, F046)."""
    rows = await db.scalars(
        select(col(Order.order_id)).where(
            col(Order.kind) == OrderKind.EXIT,
            col(Order.parent_order_id).is_(None),
            col(Order.status).in_(OPEN_ORDER_STATUSES),
        )
    )
    return list(rows.all())


async def known_client_ids(db: AsyncSession, client_order_ids: list[str]) -> set[str]:
    rows = await db.scalars(
        select(col(Order.client_order_id)).where(col(Order.client_order_id).in_(client_order_ids))
    )
    return set(rows.all())
