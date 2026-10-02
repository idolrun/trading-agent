"""The only interface to a broker (TRD §2.2). Every call goes through submit_order() (F028).
Real adapter: F051 (OD-3). Order enums are shared with the `orders` table."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from app.execution.models import OrderStatus, OrderType, Side, TimeInForce
from app.instruments import Instrument


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    instrument: Instrument
    side: Side
    qty: int
    order_type: OrderType
    tif: TimeInForce
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None


@dataclass(frozen=True)
class OcoRequest:
    """Stop + take-profit legs: a fill on one leg cancels the others (C50)."""

    client_order_id_prefix: str
    legs: list[OrderRequest]


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    instrument: Instrument
    side: Side
    qty: int
    status: OrderStatus
    filled_qty: int = 0
    avg_fill_price: Decimal | None = None


@dataclass(frozen=True)
class BrokerEvent:
    kind: Literal["status", "fill"]
    broker_order_id: str
    client_order_id: str
    status: OrderStatus
    at: datetime
    fill_id: str | None = None
    qty: int | None = None
    price: Decimal | None = None


@dataclass(frozen=True)
class BrokerPosition:
    instrument: Instrument
    qty: int
    avg_price: Decimal | None


@dataclass(frozen=True)
class BrokerAccount:
    equity: Decimal
    buying_power: Decimal
    cash: Decimal | None = None  # None = the broker does not say
    options_buying_power: Decimal | None = None  # when it differs from stock buying power


class BrokerClient(Protocol):
    async def submit(self, req: OrderRequest) -> BrokerOrder:
        """Raises BrokerTimeout / BrokerRejected / BrokerUnavailable."""
        ...

    async def submit_oco(self, req: OcoRequest) -> list[BrokerOrder]: ...

    async def get_by_client_id(self, client_order_id: str) -> BrokerOrder | None: ...

    async def cancel(self, broker_order_id: str) -> None: ...

    async def open_orders(self) -> list[BrokerOrder]: ...

    async def positions(self) -> list[BrokerPosition]: ...

    async def account(self) -> BrokerAccount:
        """Equity and buying power (C76 stock sizing)."""
        ...


class BrokerEvents(Protocol):
    """Order status and fills. OD-10: the adapter decides stream vs polling."""

    def events(self) -> AsyncIterator[BrokerEvent]: ...
