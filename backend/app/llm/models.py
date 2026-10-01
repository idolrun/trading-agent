"""Model-call audit (database-schema.md §3.5)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Uuid
from sqlmodel import Field, SQLModel

from app.db import TIMESTAMPTZ, bigint, check_in, choice, identity_pk, jsonb, smallint
from app.inbox.models import MESSAGE_EVENT_KEY, MESSAGE_KEY, EventType


class ModelProvider(StrEnum):
    TYPESAFE = "typesafe"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    OPENAI_COMPAT = "openai_compat"
    BEDROCK = "bedrock"
    LAYA = "laya"  # local typed-decision model, answers when Jev gave no answer


class ModelRole(StrEnum):
    INTERPRET = "interpret"  # Jev
    EXTRACT = "extract"
    NOTE = "note"
    PRELABEL = "prelabel"


class CallOutcome(StrEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    CONNECTION_ERROR = "connection_error"
    SCHEMA_INVALID = "schema_invalid"
    EMPTY = "empty"
    REFUSAL = "refusal"
    SCOPE_BLOCKED = "scope_blocked"
    SPAN_REJECTED = "span_rejected"
    CIRCUIT_OPEN = "circuit_open"


class ModelCall(SQLModel, table=True):
    """Immutable audit: every Jev and LLM call, with cost (C44)."""

    __tablename__ = "model_calls"
    __table_args__ = (
        ForeignKeyConstraint(MESSAGE_KEY, MESSAGE_EVENT_KEY),
        check_in("provider", ModelProvider),
        check_in("role", ModelRole),
        check_in("outcome", CallOutcome),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        Index("ix_model_calls_message", "message_id", "event_type", "event_seq"),
        Index("ix_model_calls_started_at", "started_at"),
        Index("ix_model_calls_provider_model_started", "provider", "model", "started_at"),
    )

    id: int | None = identity_pk()
    message_id: int | None = bigint(nullable=True)
    event_type: EventType | None = choice(default=None, nullable=True)
    event_seq: int | None = bigint(nullable=True)
    # Note-writer calls. use_alter: breaks the signals -> model_calls -> cards -> signals cycle.
    card_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(Uuid, ForeignKey("cards.card_id", use_alter=True))
    )
    eval_run_id: uuid.UUID | None = Field(default=None, foreign_key="eval_runs.eval_run_id")
    provider: ModelProvider = choice()
    model: str
    role: ModelRole = choice()
    attempt: int = smallint()
    prompt_version: str | None = None
    request: dict[str, Any] = jsonb()
    response: dict[str, Any] | None = jsonb(nullable=True)
    outcome: CallOutcome = choice()
    latency_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cached: int | None = None
    cost_usd: Decimal | None = Field(default=None, max_digits=12, decimal_places=6)
    started_at: datetime = Field(sa_type=TIMESTAMPTZ)
