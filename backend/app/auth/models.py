"""Auth tables: allowlisted users, sessions, append-only audit log (database-schema.md §5)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, LargeBinary
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import (
    TIMESTAMPTZ,
    check_in,
    choice,
    flag,
    identity_pk,
    inet,
    jsonb_object,
    now_default,
    timestamptz,
)


class AuditAction(StrEnum):
    USER_CREATED = "user_created"
    USER_DEACTIVATED = "user_deactivated"
    USER_REACTIVATED = "user_reactivated"
    PASSWORD_RESET = "password_reset"  # noqa: S105
    PASSWORD_CHANGED = "password_changed"  # noqa: S105
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    STEP_UP_SUCCESS = "step_up_success"
    STEP_UP_FAILURE = "step_up_failure"
    RATE_LIMITED = "rate_limited"
    TRADE_APPROVED = "trade_approved"
    TRADE_REJECTED = "trade_rejected"
    CARD_EDITED = "card_edited"
    ORDER_CREATED = "order_created"
    ORDER_CANCELLED = "order_cancelled"
    LOT_ADJUSTED = "lot_adjusted"
    KILL_SWITCH = "kill_switch"


class User(SQLModel, table=True):
    """The allowlist: only an active row here can sign in. No roles, no self-registration."""

    __tablename__ = "users"

    user_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True)  # stored lowercased
    password_hash: str
    is_active: bool = flag(default=True)
    must_change_password: bool = flag(default=True)
    created_at: datetime = now_default()
    updated_at: datetime = now_default()
    last_login_at: datetime | None = timestamptz(nullable=True)


class AuditLog(SQLModel, table=True):
    """Immutable (SELECT + INSERT only for the runtime role). `user_email` is a snapshot, and
    `user_id` is not an FK: failed logins carry no user."""

    __tablename__ = "audit_log"
    __table_args__ = (
        check_in("action", AuditAction),
        Index("ix_audit_log_at", sa_text("at DESC")),
        Index("ix_audit_log_user_at", "user_id", sa_text("at DESC")),
    )

    id: int | None = identity_pk()
    at: datetime = now_default()
    user_id: uuid.UUID | None = None
    user_email: str | None = None
    action: AuditAction = choice()
    resource_type: str | None = None
    resource_id: str | None = None
    ip: str | None = inet()
    request_id: str | None = None
    details: dict[str, Any] = jsonb_object()


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index(
            "ix_auth_sessions_live_expiry",
            "expires_at",
            postgresql_where=sa_text("revoked_at IS NULL"),
        ),
    )

    session_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    token_hash: bytes = Field(sa_type=LargeBinary, unique=True)
    # Nullable only for sessions that predate password auth (all revoked by migration 0015).
    user_id: uuid.UUID | None = Field(default=None, foreign_key="users.user_id")
    created_at: datetime = now_default()
    expires_at: datetime = Field(sa_type=TIMESTAMPTZ)
    last_seen_at: datetime = now_default()
    step_up_at: datetime | None = timestamptz(nullable=True)
    ip: str | None = inet()
    user_agent: str | None = None
    revoked_at: datetime | None = timestamptz(nullable=True)
