"""Alerts and acknowledgements (database-schema.md §5)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import bigint, check_in, choice, jsonb_object, now_default, positive_ids


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    URGENT = "urgent"


class AlertKind(StrEnum):
    FEED_SILENCE = "feed_silence"
    PARSER_DEGRADED = "parser_degraded"
    POISON = "poison"
    RECONCILE_MISMATCH = "reconcile_mismatch"
    CATCH_UP = "catch_up"
    LATE_CORRECTION = "late_correction"
    MESSAGE_DELETED = "message_deleted"
    RISK_LIMIT = "risk_limit"
    EXIT_FLOOR = "exit_floor"
    PROVIDER_DISABLED = "provider_disabled"
    MARKET_DATA = "market_data"
    INVARIANT_BLOCK = "invariant_block"
    NOTE_HIDDEN = "note_hidden"
    SYSTEM_FLAG = "system_flag"  # a safety flag was set or cleared (F008)
    AUTH_ANOMALY = "auth_anomaly"  # e.g. passkey sign-count regression (F009)
    MESSAGE_API = "message_api"  # unmappable webhook payload, history unavailable (F011, F014)
    TIMER_FAILED = "timer_failed"  # a timer handler raised (F027)
    ORDER_FAILED = "order_failed"  # broker rejected / unavailable / timeout-not-found (F028)
    ENTRY_PARTIAL = "entry_partial"  # entry remainder cancelled after M s, some filled (F041)
    UNDERLYING_STOP = "underlying_stop"  # a system-held underlying stop fired (F044)
    EOD_FORCED_CLOSE = "eod_forced_close"  # expiring lot closed at cutoff 2 (F045)
    LLM_SPEND = "llm_spend"  # month-to-date spend crossed 80 % / 100 % of the budget (F049)


class Alert(SQLModel, table=True):
    """Immutable audit. `card_id`/`lot_id`/`order_id`/`message_id` are references, not FKs."""

    __tablename__ = "alerts"
    __table_args__ = (
        check_in("severity", Severity),
        check_in("kind", AlertKind),
        *positive_ids("message_id"),
        Index("ix_alerts_created", sa_text("created_at DESC")),
    )

    alert_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    severity: Severity = choice()
    kind: AlertKind = choice()
    message: str
    card_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    message_id: int | None = bigint(nullable=True)
    details: dict[str, Any] = jsonb_object()
    dedupe_key: str | None = Field(default=None, unique=True)  # e.g. feed_silence:{episode}
    created_at: datetime = now_default()


class AlertAck(SQLModel, table=True):
    """Immutable audit: an alert is acknowledged once."""

    __tablename__ = "alert_acks"

    alert_id: uuid.UUID = Field(primary_key=True, foreign_key="alerts.alert_id")
    session_id: uuid.UUID = Field(foreign_key="auth_sessions.session_id")
    acked_at: datetime = now_default()
