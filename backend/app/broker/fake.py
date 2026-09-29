"""Deterministic, scriptable in-memory broker (tests, shadow runs, dev until F051).

Every call is recorded in `calls`. A duplicate `client_order_id` returns the existing order,
like real brokers' duplicate protection.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from app.broker.errors import BrokerRejected, BrokerTimeout, BrokerUnavailable
from app.broker.port import (
    BrokerAccount,
    BrokerEvent,
    BrokerOrder,
    BrokerPosition,
    OcoRequest,
    OrderRequest,
)
from app.execution.models import OrderStatus, Side
from app.instruments import Instrument, broker_symbol
from app.markettime import Clock, SystemClock

OPEN = (OrderStatus.SUBMITTED, OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED)


class Behavior(StrEnum):
    FILL_NOW = "fill_now"
    PARTIAL = "partial"  # fill `qty` now, rest stays working
    NEVER_FILL = "never_fill"
    REJECT = "reject"
    TIMEOUT_THEN_EXISTS = "timeout_then_exists"  # raises BrokerTimeout, order was placed
    TIMEOUT_THEN_MISSING = "timeout_then_missing"  # raises BrokerTimeout, nothing placed
    UNAVAILABLE = "unavailable"  # raises BrokerUnavailable, nothing placed


@dataclass(frozen=True)
class Script:
    behavior: Behavior
    qty: int | None = None  # PARTIAL fill size


class FakeBroker:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.default = Script(Behavior.NEVER_FILL)
        self.scripts: dict[str, Script] = {}
        self.calls: list[tuple[str, Any]] = []
        self.equity = Decimal(100_000)
        self.buying_power = Decimal(100_000)
        self._orders: dict[str, BrokerOrder] = {}  # by broker order id
        self._by_client: dict[str, str] = {}
        self._requests: dict[str, OrderRequest] = {}
        self._oco_groups: dict[str, list[str]] = {}  # prefix -> broker ids
        self._oco_of: dict[str, str] = {}
        self._positions: dict[str, tuple[Instrument, int, Decimal]] = {}  # qty, cost
        self._events: asyncio.Queue[BrokerEvent] = asyncio.Queue()
        self._seq = 0

    def script(self, client_order_id: str, behavior: Behavior, qty: int | None = None) -> None:
        self.scripts[client_order_id] = Script(behavior, qty)

    # --- BrokerClient ---

    async def submit(self, req: OrderRequest) -> BrokerOrder:
        self.calls.append(("submit", req))
        if existing := self._existing(req.client_order_id):
            return existing
        script = self.scripts.get(req.client_order_id, self.default)
        match script.behavior:
            case Behavior.UNAVAILABLE:
                raise BrokerUnavailable("scripted: broker unavailable")
            case Behavior.TIMEOUT_THEN_MISSING:
                raise BrokerTimeout("scripted: timeout, order not placed")
            case Behavior.REJECT:
                self._place(req, OrderStatus.REJECTED)
                raise BrokerRejected("scripted rejection")
            case Behavior.TIMEOUT_THEN_EXISTS:
                self._place(req, OrderStatus.WORKING)
                raise BrokerTimeout("scripted: timeout, order placed")
        order = self._place(req, OrderStatus.WORKING)
        if script.behavior is Behavior.FILL_NOW:
            order = self.fill(order.broker_order_id)
        elif script.behavior is Behavior.PARTIAL:
            order = self.fill(order.broker_order_id, qty=script.qty or 1)
        return order

    async def submit_oco(self, req: OcoRequest) -> list[BrokerOrder]:
        self.calls.append(("submit_oco", req))
        if req.client_order_id_prefix in self._oco_groups:
            return [self._orders[i] for i in self._oco_groups[req.client_order_id_prefix]]
        legs = [self._place(leg, OrderStatus.WORKING) for leg in req.legs]
        ids = [leg.broker_order_id for leg in legs]
        self._oco_groups[req.client_order_id_prefix] = ids
        for broker_order_id in ids:
            self._oco_of[broker_order_id] = req.client_order_id_prefix
        return legs

    async def get_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        self.calls.append(("get_by_client_id", client_order_id))
        return self._existing(client_order_id)

    async def cancel(self, broker_order_id: str) -> None:
        self.calls.append(("cancel", broker_order_id))
        if broker_order_id not in self._orders:
            raise BrokerRejected(f"unknown order {broker_order_id}")
        self._cancel(broker_order_id)

    async def open_orders(self) -> list[BrokerOrder]:
        return [o for o in self._orders.values() if o.status in OPEN]

    async def positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(instrument, qty, (cost / qty) if qty else None)
            for instrument, qty, cost in self._positions.values()
            if qty != 0
        ]

    async def account(self) -> BrokerAccount:
        return BrokerAccount(equity=self.equity, buying_power=self.buying_power)

    # --- BrokerEvents ---

    async def events(self) -> AsyncIterator[BrokerEvent]:
        while True:
            yield await self._events.get()

    def forget(self) -> None:
        """Drop every order, position, script and recorded call (E2E reset between tests)."""
        for table in (
            self.scripts,
            self._orders,
            self._by_client,
            self._requests,
            self._oco_groups,
            self._oco_of,
            self._positions,
        ):
            table.clear()
        self.calls.clear()
        self.pending_events()

    def pending_events(self) -> list[BrokerEvent]:
        """Drain queued events without awaiting (test helper)."""
        drained = []
        while not self._events.empty():
            drained.append(self._events.get_nowait())
        return drained

    # --- test controls ---

    def fill(
        self, broker_order_id: str, qty: int | None = None, price: Decimal | None = None
    ) -> BrokerOrder:
        """Fill `qty` (default: the rest) at `price` (default: limit, else stop price).

        A fill on an OCO leg cancels its sibling legs.
        """
        order = self._orders[broker_order_id]
        req = self._requests[broker_order_id]
        qty = qty or order.qty - order.filled_qty
        price = price or req.limit_price or req.stop_price
        if price is None or not 0 < qty <= order.qty - order.filled_qty:
            raise ValueError("fill needs a price and 0 < qty <= remaining")
        filled = order.filled_qty + qty
        avg = ((order.avg_fill_price or Decimal(0)) * order.filled_qty + price * qty) / filled
        status = OrderStatus.FILLED if filled == order.qty else OrderStatus.PARTIALLY_FILLED
        order = self._update(order, status=status, filled_qty=filled, avg_fill_price=avg)
        self._position(req.instrument, qty if req.side is Side.BUY else -qty, price)
        self._emit(order, "fill", fill_id=self._next("F"), qty=qty, price=price)
        if group := self._oco_of.get(broker_order_id):
            for sibling in self._oco_groups[group]:
                if sibling != broker_order_id:
                    self._cancel(sibling)
        return order

    def seed_position(self, instrument: Instrument, qty: int, avg_price: Decimal) -> None:
        """Set the position to `qty` at `avg_price` (e.g. a foreign holding, or a fix at the
        broker). Emits nothing: it is not an order."""
        self._positions[broker_symbol(instrument)] = (instrument, qty, avg_price * qty)

    # --- internals ---

    def _existing(self, client_order_id: str) -> BrokerOrder | None:
        broker_order_id = self._by_client.get(client_order_id)
        return self._orders[broker_order_id] if broker_order_id else None

    def _place(self, req: OrderRequest, status: OrderStatus) -> BrokerOrder:
        order = BrokerOrder(
            broker_order_id=self._next("B"),
            client_order_id=req.client_order_id,
            instrument=req.instrument,
            side=req.side,
            qty=req.qty,
            status=status,
        )
        self._orders[order.broker_order_id] = order
        self._by_client[req.client_order_id] = order.broker_order_id
        self._requests[order.broker_order_id] = req
        self._emit(order, "status")
        return order

    def _cancel(self, broker_order_id: str) -> None:
        order = self._orders[broker_order_id]
        if order.status in OPEN:
            self._emit(self._update(order, status=OrderStatus.CANCELLED), "status")

    def _update(self, order: BrokerOrder, **changes: Any) -> BrokerOrder:
        order = replace(order, **changes)
        self._orders[order.broker_order_id] = order
        return order

    def _position(self, instrument: Instrument, qty: int, price: Decimal) -> None:
        key = broker_symbol(instrument)
        _, held, cost = self._positions.get(key, (instrument, 0, Decimal(0)))
        new_qty = held + qty
        # Average cost is kept for the remaining quantity; sells reduce cost proportionally.
        new_cost = cost + price * qty if qty > 0 else (cost * new_qty / held if held else cost)
        self._positions[key] = (instrument, new_qty, new_cost)

    def _emit(self, order: BrokerOrder, kind: Literal["status", "fill"], **extra: Any) -> None:
        self._events.put_nowait(
            BrokerEvent(
                kind=kind,
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                status=order.status,
                at=self.clock.now(),
                **extra,
            )
        )

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"
