import uuid
from datetime import date, datetime
from typing import Any, Literal

from app.api.schemas.base import CamelModel, DiscordId, Num
from app.api.schemas.cards import ContractOut
from app.api.schemas.orders import OrderOut


class PositionOut(CamelModel):
    id: uuid.UUID
    provider_id: DiscordId
    opened_channel_id: DiscordId | None  # with provider_id: the lane (F068)
    instrument: str
    underlying: str
    expiry: date | None
    strike: Num | None
    option_right: str | None
    status: str
    is_corrected: bool
    provider_entry_price: Num | None
    remaining_fraction: Num
    stop_level: Num | None
    stop_basis: str | None
    tp_levels: list[Num]
    opened_at: datetime


class HistoryOut(CamelModel):
    change: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    at: datetime


class LinkOut(CamelModel):
    signal_id: uuid.UUID
    outcome: str
    method: str
    confidence: Num | None
    created_at: datetime


class LotOut(CamelModel):
    id: uuid.UUID
    provider_position_id: uuid.UUID | None
    provider_id: DiscordId | None
    contract: ContractOut
    qty: int
    avg_price: Num | None
    avg_price_label: Literal["system average"] = "system average"  # TRD §16.2 #10
    is_foreign: bool
    status: str
    opened_at: datetime


class LotEventOut(CamelModel):
    change: str
    fill_id: uuid.UUID | None
    qty_before: int | None
    qty_after: int
    avg_before: Num | None
    avg_after: Num | None
    reason: str | None
    at: datetime


class StopRowOut(CamelModel):
    id: uuid.UUID
    kind: str
    basis: str
    level: Num
    qty: int
    held_by: str
    status: str


class PositionDetail(PositionOut):
    events: list[HistoryOut]
    lots: list[LotOut]
    links: list[LinkOut]


class LotDetail(LotOut):
    events: list[LotEventOut]
    stops: list[StopRowOut]
    orders: list[OrderOut]
