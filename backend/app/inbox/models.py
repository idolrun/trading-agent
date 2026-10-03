"""Transport tables (database-schema.md §3.1-§3.3)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, SmallInteger
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import (
    TIMESTAMPTZ,
    array,
    bigint,
    check_in,
    choice,
    flag,
    jsonb,
    now_default,
    positive_ids,
    smallint,
    timestamptz,
)

MESSAGE_KEY = ("message_id", "event_type", "event_seq")
MESSAGE_EVENT_KEY = tuple(f"message_events.{c}" for c in MESSAGE_KEY)


class EventType(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"


class InboxStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    STALE = "stale"


class SkipReason(StrEnum):
    MUTED_AUTHOR = "muted_author"
    CHANNEL_NOT_CONFIGURED = "channel_not_configured"


class Stage(StrEnum):
    RECEIVED = "received"
    DEQUEUED = "dequeued"
    CONTEXT = "context"
    FAST = "fast"  # F070: settled by the deterministic fast path
    LAYA = "laya"  # F074: the local fallback answered (or timed out / was busy)
    JEV = "jev"
    LLM = "llm"
    VALIDATED = "validated"
    CARD = "card"
    PUSHED = "pushed"


class MessageEvent(SQLModel, table=True):
    """Immutable audit: every message event exactly once (FR-1); raw payload kept 7 years."""

    __tablename__ = "message_events"
    __table_args__ = (
        check_in("event_type", EventType),
        CheckConstraint("event_seq >= 0", name="event_seq_non_negative"),
        CheckConstraint("event_type = 'delete' OR text IS NOT NULL", name="text_unless_delete"),
        *positive_ids(
            "message_id", "provider_id", "channel_id", "reply_to_id", "thread_id", "guild_id"
        ),
        Index("ix_message_events_provider_posted", "provider_id", sa_text("posted_at DESC")),
        Index(
            "ix_message_events_lane_posted",
            "channel_id",
            "provider_id",
            sa_text("posted_at DESC"),
        ),
        Index("ix_message_events_thread_id", "thread_id"),
        Index("ix_message_events_channel_message", "channel_id", sa_text("message_id DESC")),
        Index("ix_message_events_reply_to_id", "reply_to_id"),
        Index("ix_message_events_posted_at", "posted_at"),
    )

    message_id: int = Field(sa_type=BigInteger, primary_key=True)
    event_type: EventType = choice(primary_key=True)
    event_seq: int = Field(sa_type=BigInteger, primary_key=True)
    provider_id: int = bigint()
    channel_id: int = bigint()  # context channel: a thread message carries its parent's id
    thread_id: int | None = bigint(nullable=True)
    guild_id: int | None = bigint(nullable=True)
    posted_at: datetime = Field(sa_type=TIMESTAMPTZ)
    edited_at: datetime | None = timestamptz(nullable=True)
    reply_to_id: int | None = bigint(nullable=True)
    mentioned_author_ids: list[int] = array(BigInteger)
    text: str | None = None
    payload: dict[str, Any] = jsonb()
    backfilled: bool = flag()
    received_at: datetime = now_default()


class InboxItem(SQLModel, table=True):
    """Current state: transport queue (TRD §2.4)."""

    __tablename__ = "inbox"
    __table_args__ = (
        ForeignKeyConstraint(MESSAGE_KEY, MESSAGE_EVENT_KEY),
        check_in("status", InboxStatus),
        check_in("skip_reason", SkipReason),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index(
            "ix_inbox_next_for_lane",
            "provider_id",
            "channel_id",
            "posted_at",
            "message_id",
            postgresql_where=sa_text("status IN ('received', 'processing')"),
        ),
        Index("ix_inbox_backlog", "posted_at", postgresql_where=sa_text("status = 'received'")),
        Index(
            "ix_inbox_applied",
            "provider_id",
            "channel_id",
            sa_text("posted_at DESC"),
            postgresql_where=sa_text("status IN ('done', 'stale')"),
        ),
    )

    message_id: int = Field(sa_type=BigInteger, primary_key=True)
    event_type: EventType = choice(primary_key=True)
    event_seq: int = Field(sa_type=BigInteger, primary_key=True)
    provider_id: int = bigint()
    channel_id: int = bigint()  # with provider_id: the lane (sequencing unit, TRD §2.4)
    posted_at: datetime = Field(sa_type=TIMESTAMPTZ)
    status: InboxStatus = choice()
    attempts: int = smallint(default=0)
    last_error: str | None = None
    late: bool = flag()
    backfilled: bool = flag()
    skip_reason: SkipReason | None = choice(default=None, nullable=True)
    claimed_at: datetime | None = timestamptz(nullable=True)
    finished_at: datetime | None = timestamptz(nullable=True)
    updated_at: datetime = now_default()


class StageTiming(SQLModel, table=True):
    """Immutable audit: per-stage timestamps (deviation X7)."""

    __tablename__ = "stage_timings"
    __table_args__ = (
        ForeignKeyConstraint(MESSAGE_KEY, MESSAGE_EVENT_KEY),
        check_in("stage", Stage),
        Index("ix_stage_timings_stage_at", "stage", "at"),
    )

    message_id: int = Field(sa_type=BigInteger, primary_key=True)
    event_type: EventType = choice(primary_key=True)
    event_seq: int = Field(sa_type=BigInteger, primary_key=True)
    attempt: int = Field(sa_type=SmallInteger, primary_key=True)
    stage: Stage = choice(primary_key=True)
    at: datetime = now_default()
