"""Evaluation labels and runs (database-schema.md §5, C64, C67)."""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import TIMESTAMPTZ, bigint, check_in, choice, jsonb, jsonb_object, now_default
from app.inbox.models import MESSAGE_EVENT_KEY, MESSAGE_KEY, EventType


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"


class LabelSource(StrEnum):
    PRELABEL_CORRECTED = "prelabel_corrected"
    MANUAL = "manual"
    PROVIDER_CORRECTION = "provider_correction"


class EvalRunKind(StrEnum):
    REPLAY_GATE = "replay_gate"
    THRESHOLD_SWEEP = "threshold_sweep"
    BENCH = "bench"


class EvalLabel(SQLModel, table=True):
    """Immutable audit: expected `ParsedSignals` + link per message event; corrections supersede."""

    __tablename__ = "eval_labels"
    __table_args__ = (
        ForeignKeyConstraint(MESSAGE_KEY, MESSAGE_EVENT_KEY),
        check_in("split", Split),
        check_in("source", LabelSource),
        Index("ix_eval_labels_split_day", "split", "trading_day"),
    )

    label_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    message_id: int = bigint()
    event_type: EventType = choice()
    event_seq: int = bigint()
    label: dict[str, Any] = jsonb()
    trading_day: date
    split: Split = choice()
    source: LabelSource = choice()
    supersedes_label_id: uuid.UUID | None = Field(default=None, foreign_key="eval_labels.label_id")
    session_id: uuid.UUID | None = Field(default=None, foreign_key="auth_sessions.session_id")
    labeled_at: datetime = now_default()


class EvalRun(SQLModel, table=True):
    """Immutable audit: written once, when the run has finished."""

    __tablename__ = "eval_runs"
    __table_args__ = (
        check_in("kind", EvalRunKind),
        check_in("split", Split),
        Index("ix_eval_runs_model_started", "model_ref", sa_text("started_at DESC")),
    )

    eval_run_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    kind: EvalRunKind = choice()
    model_ref: str  # provider:model:prompt
    split: Split | None = choice(default=None, nullable=True)
    thresholds: dict[str, Any] = jsonb_object()
    metrics: dict[str, Any] = jsonb()
    gate_passed: bool | None = None
    started_at: datetime = Field(sa_type=TIMESTAMPTZ)
    finished_at: datetime = Field(sa_type=TIMESTAMPTZ)
