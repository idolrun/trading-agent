"""Broker order-status and fill intake (F040, TRD §6, database-schema.md T6).

One transaction per broker event, idempotent: a fill is keyed by `broker_fill_id` (stream and
poll may both deliver it), status events never move an order backwards (they can arrive late
or twice). The broker is the source of truth; the lot ledger follows the fills exactly.
Events for orders that are not ours are ignored here (reconciliation treats them as foreign).
"""

import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.broker.port import BrokerEvent
from app.db import notify
from app.execution import repo
from app.execution.models import Fill, Order, OrderEventSource, OrderStatus, Side
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.ledger.models import Lot, ProviderPosition
from app.ledger.positions import contract_of, state_of

logger = logging.getLogger(__name__)
TERMINAL = frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED})
RANK = {
    OrderStatus.INTENT: 0,
    OrderStatus.UNKNOWN: 1,
    OrderStatus.SUBMITTED: 1,
    OrderStatus.WORKING: 2,
    OrderStatus.PARTIALLY_FILLED: 3,
}


def next_status(current: OrderStatus, incoming: OrderStatus) -> OrderStatus | None:
    """TRD §6, forward only: terminal states are final, nothing moves back. None = ignore."""
    if current in TERMINAL or incoming == current:
        return None
    if incoming in TERMINAL:
        return incoming
    return incoming if RANK[incoming] > RANK[current] else None


@dataclass(frozen=True)
class Applied:
    """A new fill that changed the books (input to the `on_fill` hooks)."""

    order: Order
    fill: Fill
    lot: Lot | None


async def apply_broker_event(
    db: AsyncSession, event: BrokerEvent, source: OrderEventSource
) -> Applied | None:
    """Call inside one transaction (T6). Returns the applied fill, None otherwise."""
    order = await _lock_order(db, event)
    if order is None:
        logger.info("broker event for an unknown order", extra={"coid": event.client_order_id})
        return None
    if event.kind == "status":
        await _status(db, order, event, source)
        return None
    return await _fill(db, order, event, source)


async def _status(
    db: AsyncSession, order: Order, event: BrokerEvent, source: OrderEventSource
) -> None:
    status = next_status(OrderStatus(order.status), event.status)
    if status is None:
        return
    await repo.set_status(
        db,
        order,
        status,
        source,
        broker_order_id=order.broker_order_id or event.broker_order_id,
        payload={"broker_status": event.status},
    )
    await notify(db, "orders", _event("order.updated", order.order_id))


async def _fill(
    db: AsyncSession, order: Order, event: BrokerEvent, source: OrderEventSource
) -> Applied | None:
    if event.fill_id is None or event.qty is None or event.price is None:
        logger.error("fill event without id/qty/price", extra={"coid": event.client_order_id})
        return None
    fill_id = await db.scalar(
        pg_insert(Fill)
        .values(
            fill_id=uuid.uuid4(),
            order_id=order.order_id,
            broker_fill_id=event.fill_id,
            qty=event.qty,
            price=event.price,
            filled_at=event.at,
        )
        .on_conflict_do_nothing(index_elements=["broker_fill_id"])
        .returning(col(Fill.fill_id))
    )
    if fill_id is None:
        return None  # delivered before (stream + poll, or a replay): exactly once
    fill = await db.get(Fill, fill_id)
    assert fill is not None  # noqa: S101 — just inserted

    lot = await _apply_to_lot(db, order, fill)
    # from the fills themselves: submit may already have copied the broker's snapshot
    qty, notional = (
        await db.execute(
            select(func.sum(col(Fill.qty)), func.sum(col(Fill.qty) * col(Fill.price))).where(
                col(Fill.order_id) == order.order_id
            )
        )
    ).one()
    order.filled_qty = min(int(qty), order.qty)
    order.avg_fill_price = (Decimal(notional) / int(qty)).quantize(Decimal("0.0001"))
    status = OrderStatus.FILLED if order.filled_qty == order.qty else OrderStatus.PARTIALLY_FILLED
    # a late fill of a cancelled order keeps `cancelled` (partial fills are kept, FR-21)
    status = next_status(OrderStatus(order.status), status) or OrderStatus(order.status)
    await repo.set_status(
        db,
        order,
        status,
        source,
        broker_order_id=order.broker_order_id or event.broker_order_id,
        filled_qty=order.filled_qty,
        payload={"fill_id": event.fill_id, "qty": fill.qty, "price": str(fill.price)},
    )
    await notify(db, "fills", _event("fill.created", fill.fill_id))
    await notify(db, "orders", _event("order.updated", order.order_id))
    if lot is not None:
        await notify(db, "lots", _event("lot.updated", lot.lot_id))
    return Applied(order, fill, lot)


async def _apply_to_lot(db: AsyncSession, order: Order, fill: Fill) -> Lot | None:
    """Entry buys open or grow the position's lot (FR-16: re-entry after close = new lot);
    sells reduce the order's lot. Invariants (F028) kept sells within the lot."""
    side: Literal["buy", "sell"] = "buy" if order.side == Side.BUY else "sell"
    lot = await ledger.lock_lot(db, order.lot_id) if order.lot_id else None
    if side == "buy" and (lot is None or lot.qty == 0) and order.provider_position_id:
        lot = await ledger.open_lot_for_position(db, order.provider_position_id)
        lot = await ledger.lock_lot(db, lot.lot_id) if lot else None
    if lot is None and side == "sell":
        logger.error("sell fill without a lot", extra={"order_id": str(order.order_id)})
        return None
    held = (lot.qty, lot.avg_price) if lot is not None and lot.qty > 0 else None
    try:
        update = apply_fill(held, side, fill.qty, fill.price)
    except ValueError:
        logger.exception("fill does not fit the lot", extra={"order_id": str(order.order_id)})
        return None  # reconciliation (F046) reports the mismatch
    instrument, provider_id = None, None
    if lot is None or lot.qty == 0:
        position = (
            await db.get(ProviderPosition, order.provider_position_id)
            if order.provider_position_id
            else None
        )
        if position is None:
            logger.error("buy fill without a provider position", extra={"id": str(order.order_id)})
            return None
        instrument, provider_id = contract_of(state_of(position)), position.provider_id
        lot = None  # a closed lot is never reopened: a re-entry is a new lot
    row = await ledger.record_lot(
        db,
        update,
        row=lot,
        instrument=instrument,
        provider_position_id=order.provider_position_id,
        provider_id=provider_id,
        fill_id=fill.fill_id,
        reason=f"fill {fill.broker_fill_id}",
    )
    if order.lot_id != row.lot_id:
        order.lot_id = row.lot_id
    return row


async def _lock_order(db: AsyncSession, event: BrokerEvent) -> Order | None:
    order: Order | None = await db.scalar(
        select(Order)
        .where(
            or_(
                col(Order.broker_order_id) == event.broker_order_id,
                col(Order.client_order_id) == event.client_order_id,
            )
        )
        .with_for_update()
    )
    return order


def _event(kind: str, id_: uuid.UUID) -> str:
    return json.dumps({"type": kind, "id": str(id_)})
