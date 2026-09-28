"""Persisted deadlines (database-schema.md §4.13). Scheduler is F027."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKeyConstraint, Index
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import TIMESTAMPTZ, bigint, check_in, choice, now_default, timestamptz
from app.inbox.models import MESSAGE_EVENT_KEY, MESSAGE_KEY, EventType


class TimerKind(StrEnum):
    CARD_EXPIRY = "card_expiry"
    ENTRY_CANCEL = "entry_cancel"
    CUTOFF_1 = "cutoff_1"
    CUTOFF_2 = "cutoff_2"
    OFFHOURS_RELEASE = "offhours_release"


class TimerStatus(StrEnum):
    SCHEDULED = "scheduled"
    FIRED = "fired"
    CANCELLED = "cancelled"


class Timer(SQLModel, table=True):
    __tablename__ = "timers"
    __table_args__ = (
        ForeignKeyConstraint(MESSAGE_KEY, MESSAGE_EVENT_KEY),
        check_in("kind", TimerKind),
        check_in("status", TimerStatus),
        Index("ix_timers_due", "due_at", postgresql_where=sa_text("status = 'scheduled'")),
    )

    timer_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    kind: TimerKind = choice()
    due_at: datetime = Field(sa_type=TIMESTAMPTZ)
    card_id: uuid.UUID | None = Field(default=None, foreign_key="cards.card_id")
    lot_id: uuid.UUID | None = Field(default=None, foreign_key="lots.lot_id")
    order_id: uuid.UUID | None = Field(default=None, foreign_key="orders.order_id")
    message_id: int | None = bigint(nullable=True)
    event_type: EventType | None = choice(default=None, nullable=True)
    event_seq: int | None = bigint(nullable=True)
    dedupe_key: str = Field(unique=True)
    status: TimerStatus = choice()
    fired_at: datetime | None = timestamptz(nullable=True)
    created_at: datetime = now_default()
