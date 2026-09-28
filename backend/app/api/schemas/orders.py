import uuid
from datetime import datetime
from typing import Any

from app.api.schemas.base import CamelModel, Num


class OrderOut(CamelModel):
    id: uuid.UUID
    card_id: uuid.UUID | None
    lot_id: uuid.UUID | None
    kind: str
    side: str
    status: str
    client_order_id: str
    broker_symbol: str
    qty: int
    filled_qty: int
    limit_price: Num | None
    stop_price: Num | None
    mode: str
    auto_reason: str | None
    created_at: datetime


class OrderEventOut(CamelModel):
    status: str
    source: str
    broker_payload: dict[str, Any] | None
    at: datetime


class FillOut(CamelModel):
    id: uuid.UUID
    order_id: uuid.UUID
    qty: int
    price: Num
    filled_at: datetime


class OrderDetail(OrderOut):
    events: list[OrderEventOut]
    fills: list[FillOut]
