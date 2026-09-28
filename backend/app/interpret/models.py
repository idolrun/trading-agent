"""Parsed signals and correlation links (database-schema.md §3.6, §3.7)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import (
    array,
    bigint,
    check_in,
    choice,
    identity_pk,
    jsonb,
    now_default,
    positive_ids,
    smallint,
)
from app.inbox.models import MESSAGE_EVENT_KEY, MESSAGE_KEY, EventType


class MsgType(StrEnum):
    """FR-5 message types, plus `unparsed` (raw review card: poison / parser degraded)."""

    ENTRY = "entry"
    ADD = "add"
    TRIM = "trim"
    FULL_EXIT = "full_exit"
    SOFT_EXIT = "soft_exit"
    STOP_SET = "stop_set"
    STOP_MOVE = "stop_move"
    STOP_CANCEL = "stop_cancel"
    TP_SET = "tp_set"
    TP_HIT_NOTICE = "tp_hit_notice"
    CORRECTION = "correction"
    WATCH = "watch"
    CONDITIONAL = "conditional"
    COMMENTARY_PNL = "commentary_pnl"
    STOCK_ENTRY = "stock_entry"
    STOCK_EXIT = "stock_exit"
    OUT_OF_SCOPE_STRATEGY = "out_of_scope_strategy"
    CHATTER = "chatter"
    UNPARSED = "unparsed"


class SignalInstrument(StrEnum):
    OPTION = "option"
    STOCK = "stock"
    UNCLEAR = "unclear"


class Interpreter(StrEnum):
    JEV = "jev"
    LLM = "llm"
    NONE = "none"


class LinkOutcome(StrEnum):
    LINKED = "linked"
    NEW_POSITION = "new_position"
    NONE = "none"
    AMBIGUOUS = "ambiguous"


class LinkMethod(StrEnum):
    REPLY = "reply"
    JEV = "jev"
    LLM = "llm"
    USER_CORRECTION = "user_correction"
    CODE = "code"  # entry opened a new position


class Signal(SQLModel, table=True):
    """Immutable audit: parsed signals with provenance (FR-6, FR-7)."""

    __tablename__ = "signals"
    __table_args__ = (
        ForeignKeyConstraint(MESSAGE_KEY, MESSAGE_EVENT_KEY),
        UniqueConstraint(*MESSAGE_KEY, "idx"),
        UniqueConstraint("signal_id", "provider_id"),  # composite FK target (no cross-provider)
        check_in("msg_type", MsgType),
        check_in("instrument", SignalInstrument),
        check_in("interpreter", Interpreter),
        CheckConstraint("idx >= 0", name="idx_non_negative"),
        *positive_ids("provider_id"),
        Index("ix_signals_provider_created", "provider_id", sa_text("created_at DESC")),
    )

    signal_id: uuid.UUID = Field(primary_key=True)
    message_id: int = bigint()
    event_type: EventType = choice()
    event_seq: int
    idx: int = smallint()
    provider_id: int = bigint()
    msg_type: MsgType = choice()
    instrument: SignalInstrument | None = choice(default=None, nullable=True)
    fields: dict[str, Any] = jsonb()
    interpreter: Interpreter = choice()
    model_call_id: int | None = bigint(nullable=True, foreign_key="model_calls.id")
    stale: bool
    late: bool
    schema_version: int = Field(sa_type=SmallInteger)
    created_at: datetime = now_default()


class MessageLink(SQLModel, table=True):
    """Immutable audit: signal → provider position correlation; current link = latest row."""

    __tablename__ = "message_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["signal_id", "provider_id"], ["signals.signal_id", "signals.provider_id"]
        ),
        ForeignKeyConstraint(
            ["provider_position_id", "provider_id"],
            ["provider_positions.provider_position_id", "provider_positions.provider_id"],
        ),
        check_in("outcome", LinkOutcome),
        check_in("method", LinkMethod),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint(
            "(outcome IN ('linked', 'new_position')) = (provider_position_id IS NOT NULL)",
            name="position_iff_linked",
        ),
        Index("ix_message_links_signal_latest", "signal_id", sa_text("id DESC")),
        Index("ix_message_links_provider_position_id", "provider_position_id"),
    )

    id: int | None = identity_pk()
    signal_id: uuid.UUID
    provider_id: int = bigint()
    provider_position_id: uuid.UUID | None = None
    outcome: LinkOutcome = choice()
    method: LinkMethod = choice()
    confidence: Decimal | None = Field(default=None, max_digits=5, decimal_places=4)
    candidate_position_ids: list[uuid.UUID] = array(Uuid)
    supersedes_link_id: int | None = bigint(nullable=True, foreign_key="message_links.id")
    created_at: datetime = now_default()
