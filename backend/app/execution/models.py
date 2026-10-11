"""Orders, fills, stops/TPs and their history (database-schema.md §4.8-§4.12)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Index, SmallInteger
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import (
    TIMESTAMPTZ,
    check_in,
    choice,
    flag,
    identity_pk,
    jsonb,
    jsonb_object,
    now_default,
    price,
)
from app.ledger.models import Instrument


class OrderKind(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    STOP = "stop"
    TP = "tp"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    STOP_MARKET = "stop_market"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"


class OrderStatus(StrEnum):
    INTENT = "intent"
    SUBMITTED = "submitted"
    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


OPEN_ORDER_STATUSES = tuple(
    s.value
    for s in (
        OrderStatus.INTENT,
        OrderStatus.SUBMITTED,
        OrderStatus.WORKING,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.UNKNOWN,
    )
)


class Mode(StrEnum):
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


class AutoReason(StrEnum):
    """The only order actions allowed without a card approval (PRD §9, AC-2)."""

    REPRICE = "reprice"
    EOD_FORCED_CLOSE = "eod_forced_close"
    STOP_RESTORE = "stop_restore"
    UNDERLYING_STOP = "underlying_stop"


class OrderEventSource(StrEnum):
    SUBMIT = "submit"
    BROKER_STREAM = "broker_stream"
    POLL = "poll"
    RECONCILE = "reconcile"
    TIMEOUT_QUERY = "timeout_query"
    CANCEL = "cancel"


class StopKind(StrEnum):
    STOP = "stop"
    TP = "tp"


class StopLevelBasis(StrEnum):
    PREMIUM = "premium"
    UNDERLYING = "underlying"
    STOCK = "stock"


class HeldBy(StrEnum):
    BROKER = "broker"
    SYSTEM = "system"


class StopStatus(StrEnum):
    PENDING = "pending"
    WORKING = "working"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    FIRED = "fired"


class StopChange(StrEnum):
    CREATE = "create"
    WORKING = "working"
    SUSPEND = "suspend"
    RESTORE = "restore"
    MOVE = "move"
    CANCEL = "cancel"
    FIRE = "fire"
    REDUCE = "reduce"


class Order(SQLModel, table=True):
    """Current state. Every order is approved or carries an allowed auto reason (AC-2)."""

    __tablename__ = "orders"
    __table_args__ = (
        check_in("auto_reason", AutoReason),
        check_in("kind", OrderKind),
        check_in("side", Side),
        check_in("instrument", Instrument),
        check_in("order_type", OrderType),
        check_in("tif", TimeInForce),
        check_in("status", OrderStatus),
        check_in("mode", Mode),
        CheckConstraint(
            "approval_id IS NOT NULL OR auto_reason IS NOT NULL", name="approved_or_auto"
        ),
        CheckConstraint("(kind = 'entry') = (side = 'buy')", name="no_short_shape"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("filled_qty BETWEEN 0 AND qty", name="filled_qty_range"),
        Index(
            "ix_orders_open_status",
            "status",
            postgresql_where=sa_text(f"status IN {OPEN_ORDER_STATUSES}"),
        ),
        Index("ix_orders_lot_status", "lot_id", "status"),
        Index("ix_orders_card_id", "card_id"),
    )

    order_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    client_order_id: str = Field(unique=True)
    card_id: uuid.UUID | None = Field(default=None, foreign_key="cards.card_id")
    approval_id: uuid.UUID | None = Field(default=None, foreign_key="approvals.approval_id")
    auto_reason: AutoReason | None = choice(default=None, nullable=True)
    parent_order_id: uuid.UUID | None = Field(default=None, foreign_key="orders.order_id")
    oco_group_id: uuid.UUID | None = None
    provider_position_id: uuid.UUID | None = Field(
        default=None, foreign_key="provider_positions.provider_position_id"
    )
    lot_id: uuid.UUID | None = Field(default=None, foreign_key="lots.lot_id")
    kind: OrderKind = choice()
    side: Side = choice()
    instrument: Instrument = choice()
    broker_symbol: str
    qty: int
    order_type: OrderType = choice()
    limit_price: Decimal | None = price()
    stop_price: Decimal | None = price()
    tif: TimeInForce = choice()
    status: OrderStatus = choice()
    mode: Mode = choice()
    broker_order_id: str | None = Field(default=None, unique=True)
    filled_qty: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    avg_fill_price: Decimal | None = price()
    created_at: datetime = now_default()
    updated_at: datetime = now_default()


class OrderEvent(SQLModel, table=True):
    """Immutable audit: order status history."""

    __tablename__ = "order_events"
    __table_args__ = (
        check_in("status", OrderStatus),
        check_in("source", OrderEventSource),
        Index("ix_order_events_order", "order_id", "id"),
    )

    id: int | None = identity_pk()
    order_id: uuid.UUID = Field(foreign_key="orders.order_id")
    status: OrderStatus = choice()
    source: OrderEventSource = choice()
    broker_payload: dict[str, Any] | None = jsonb(nullable=True)
    at: datetime = now_default()


class Fill(SQLModel, table=True):
    """Immutable audit. `broker_fill_id` dedupes stream + poll delivery."""

    __tablename__ = "fills"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        Index("ix_fills_order_id", "order_id"),
        Index("ix_fills_filled_at", "filled_at"),  # reports window every fill by time (F083)
    )

    fill_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_id: uuid.UUID = Field(foreign_key="orders.order_id")
    broker_fill_id: str = Field(unique=True)
    qty: int
    price: Decimal = price(nullable=False)
    filled_at: datetime = Field(sa_type=TIMESTAMPTZ)
    received_at: datetime = now_default()


class Stop(SQLModel, table=True):
    """Current state: stops and take-profits. Underlying-basis stops are system-held (C49)."""

    __tablename__ = "stops"
    __table_args__ = (
        check_in("kind", StopKind),
        check_in("basis", StopLevelBasis),
        check_in("held_by", HeldBy),
        check_in("status", StopStatus),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("tp_index >= 1", name="tp_index_positive"),
        CheckConstraint(
            "(held_by = 'system') = (basis = 'underlying')", name="system_iff_underlying"
        ),
        CheckConstraint("kind <> 'tp' OR tp_index IS NOT NULL", name="tp_has_index"),
        Index("ix_stops_lot_status", "lot_id", "status"),
        Index(
            "ix_stops_system_working",
            "status",
            postgresql_where=sa_text("held_by = 'system' AND status = 'working'"),
        ),
    )

    stop_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    lot_id: uuid.UUID = Field(foreign_key="lots.lot_id")
    kind: StopKind = choice()
    basis: StopLevelBasis = choice()
    held_by: HeldBy = choice()
    level: Decimal = price(nullable=False)
    qty: int
    tp_index: int | None = Field(default=None, sa_type=SmallInteger)
    order_id: uuid.UUID | None = Field(default=None, foreign_key="orders.order_id")
    card_id: uuid.UUID = Field(foreign_key="cards.card_id")
    candle_close: bool = flag()
    status: StopStatus = choice()
    created_at: datetime = now_default()
    updated_at: datetime = now_default()


class StopEvent(SQLModel, table=True):
    """Immutable audit: stop / TP history (OD-13)."""

    __tablename__ = "stop_events"
    __table_args__ = (
        check_in("change", StopChange),
        Index("ix_stop_events_stop", "stop_id", "id"),
    )

    id: int | None = identity_pk()
    stop_id: uuid.UUID = Field(foreign_key="stops.stop_id")
    change: StopChange = choice()
    order_id: uuid.UUID | None = Field(default=None, foreign_key="orders.order_id")
    detail: dict[str, Any] = jsonb_object()
    at: datetime = now_default()
