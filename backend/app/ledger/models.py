"""Provider ledger and user lots (database-schema.md §4.1-§4.4)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Numeric, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import (
    array,
    bigint,
    check_in,
    choice,
    flag,
    identity_pk,
    jsonb,
    now_default,
    positive_ids,
    price,
    timestamptz,
)


class Instrument(StrEnum):
    OPTION = "option"
    STOCK = "stock"


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"


class StopBasis(StrEnum):
    PREMIUM = "premium"
    UNDERLYING = "underlying"


class PositionStatus(StrEnum):
    WATCH = "watch"  # F072: a setup the provider is watching; never a card, never a lot
    CONDITIONAL = "conditional"  # F072: an entry that waits on a condition in words (C30)
    OPEN = "open"
    TRIMMED = "trimmed"
    CLOSED = "closed"


class PositionChange(StrEnum):
    OPEN = "open"
    ADD = "add"
    TRIM = "trim"
    EXIT = "exit"
    STOP_SET = "stop_set"
    STOP_MOVE = "stop_move"
    STOP_CANCEL = "stop_cancel"
    TP_SET = "tp_set"
    CORRECT = "correct"
    CLOSE = "close"
    WATCH = "watch"  # F072: a watch setup opened or restated
    CONDITION_SET = "condition_set"  # F072: a conditional setup opened or restated


class LotStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class LotChange(StrEnum):
    OPEN = "open"
    FILL_ADD = "fill_add"
    FILL_REDUCE = "fill_reduce"
    CLOSE = "close"
    RECONCILE_ADJUST = "reconcile_adjust"
    ADOPT_FOREIGN = "adopt_foreign"


def contract_shape(setups: bool = False) -> CheckConstraint:
    """Options carry expiry/strike/right; stocks carry none of them. With `setups`, a
    watch/conditional provider position may name only part of a contract (F072)."""
    complete = (
        "(instrument = 'option' AND expiry IS NOT NULL AND strike IS NOT NULL"
        " AND option_right IS NOT NULL)"
        " OR (instrument = 'stock' AND expiry IS NULL AND strike IS NULL"
        " AND option_right IS NULL)"
    )
    if setups:
        complete = f"status IN ('watch', 'conditional') OR {complete}"
    return CheckConstraint(complete, name="contract_shape")


class ProviderPosition(SQLModel, table=True):
    """Current state: a provider's position as stated in their messages; one lane only
    (`provider_id`, `opened_channel_id`, F068). A row with no channel is in no lane's book."""

    __tablename__ = "provider_positions"
    __table_args__ = (
        UniqueConstraint("provider_position_id", "provider_id"),  # composite FK target
        check_in("instrument", Instrument),
        check_in("option_right", OptionRight),
        check_in("stop_basis", StopBasis),
        check_in("status", PositionStatus),
        contract_shape(setups=True),
        CheckConstraint(
            "instrument IS NOT NULL OR status IN ('watch', 'conditional')",
            name="instrument_unless_setup",
        ),
        CheckConstraint("remaining_fraction BETWEEN 0 AND 1", name="remaining_fraction_range"),
        *positive_ids("provider_id", "opened_channel_id"),
        Index(
            "ix_provider_positions_open_book",
            "provider_id",
            "opened_channel_id",
            "status",
            postgresql_where=sa_text("status <> 'closed'"),
        ),
        Index(
            "ix_provider_positions_recent_underlyings",
            "provider_id",
            "underlying",
            sa_text("opened_at DESC"),
        ),
    )

    provider_position_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    provider_id: int = bigint()
    opened_channel_id: int | None = bigint(nullable=True)
    instrument: Instrument | None = choice(default=None, nullable=True)  # None: a setup only
    underlying: str
    expiry: date | None = None
    strike: Decimal | None = price()
    option_right: OptionRight | None = choice(default=None, nullable=True)
    provider_entry_price: Decimal | None = price()
    remaining_fraction: Decimal = Field(
        default=Decimal(1), max_digits=5, decimal_places=4, sa_column_kwargs={"server_default": "1"}
    )
    stop_level: Decimal | None = price()
    stop_basis: StopBasis | None = choice(default=None, nullable=True)
    tp_levels: list[Decimal] = array(Numeric(12, 4))
    status: PositionStatus = choice()
    is_corrected: bool = flag()
    condition_text: str | None = None  # F072: the provider's words for a conditional; shown only
    reentry_of_position_id: uuid.UUID | None = Field(  # F072: "back in" after this position
        default=None, foreign_key="provider_positions.provider_position_id"
    )
    opened_by_signal_id: uuid.UUID | None = Field(default=None, foreign_key="signals.signal_id")
    opened_at: datetime = now_default()
    closed_at: datetime | None = timestamptz(nullable=True)
    updated_at: datetime = now_default()


class ProviderPositionEvent(SQLModel, table=True):
    """Immutable audit: provider position history (OD-13), caused by a signal or a correction."""

    __tablename__ = "provider_position_events"
    __table_args__ = (
        check_in("change", PositionChange),
        CheckConstraint(
            "num_nonnulls(signal_id, manual_correction_id) = 1", name="exactly_one_cause"
        ),
        Index("ix_provider_position_events_position", "provider_position_id", "id"),
    )

    id: int | None = identity_pk()
    provider_position_id: uuid.UUID = Field(foreign_key="provider_positions.provider_position_id")
    change: PositionChange = choice()
    signal_id: uuid.UUID | None = Field(default=None, foreign_key="signals.signal_id")
    manual_correction_id: int | None = bigint(nullable=True, foreign_key="manual_corrections.id")
    before: dict[str, Any] | None = jsonb(nullable=True)
    after: dict[str, Any] = jsonb()
    at: datetime = now_default()


class Lot(SQLModel, table=True):
    """Current state: the user's holding. Foreign lots (C47) have no provider position."""

    __tablename__ = "lots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_position_id", "provider_id"],
            ["provider_positions.provider_position_id", "provider_positions.provider_id"],
        ),
        check_in("instrument", Instrument),
        check_in("option_right", OptionRight),
        check_in("status", LotStatus),
        contract_shape(),
        CheckConstraint("qty >= 0", name="qty_non_negative"),
        CheckConstraint(
            "is_foreign = (provider_position_id IS NULL)", name="foreign_iff_no_position"
        ),
        CheckConstraint(
            "(provider_id IS NULL) = (provider_position_id IS NULL)", name="provider_with_position"
        ),
        CheckConstraint("(status = 'closed') = (qty = 0)", name="closed_iff_empty"),
        Index(
            "uq_lots_one_open_per_position",
            "provider_position_id",
            unique=True,
            postgresql_where=sa_text("status = 'open'"),
        ),
        Index("ix_lots_broker_symbol_status", "broker_symbol", "status"),
        Index("ix_lots_underlying_status", "underlying", "status"),
        Index("ix_lots_open_expiry", "expiry", postgresql_where=sa_text("status = 'open'")),
        Index(  # reports: trades closed in a window (F083)
            "ix_lots_closed_at", "closed_at", postgresql_where=sa_text("closed_at IS NOT NULL")
        ),
    )

    lot_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    provider_position_id: uuid.UUID | None = None
    provider_id: int | None = bigint(nullable=True)
    is_foreign: bool
    instrument: Instrument = choice()
    broker_symbol: str  # OCC option symbol or stock ticker
    underlying: str
    expiry: date | None = None
    strike: Decimal | None = price()
    option_right: OptionRight | None = choice(default=None, nullable=True)
    qty: int
    avg_price: Decimal | None = price()
    status: LotStatus = choice()
    opened_at: datetime = now_default()
    closed_at: datetime | None = timestamptz(nullable=True)
    updated_at: datetime = now_default()


class LotEvent(SQLModel, table=True):
    """Immutable audit: history of lots (OD-13)."""

    __tablename__ = "lot_events"
    __table_args__ = (
        check_in("change", LotChange),
        Index("ix_lot_events_lot", "lot_id", "id"),
    )

    id: int | None = identity_pk()
    lot_id: uuid.UUID = Field(foreign_key="lots.lot_id")
    change: LotChange = choice()
    fill_id: uuid.UUID | None = Field(default=None, foreign_key="fills.fill_id")
    qty_before: int | None = None
    qty_after: int
    avg_before: Decimal | None = price()
    avg_after: Decimal | None = price()
    reason: str | None = None
    at: datetime = now_default()
