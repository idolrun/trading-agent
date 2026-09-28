"""Passkey auth tables (database-schema.md §5, PRD §13). Only hashes / public keys are stored."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Index, LargeBinary, Text
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import TIMESTAMPTZ, array, check_in, choice, identity_pk, inet, now_default, timestamptz


class LoginOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    STEP_UP_SUCCESS = "step_up_success"
    STEP_UP_FAILURE = "step_up_failure"
    LOGOUT = "logout"
    RATE_LIMITED = "rate_limited"
    ENROLLED = "enrolled"


class Login(SQLModel, table=True):
    """Immutable audit. `credential_id` is not an FK: failed attempts may carry unknown IDs."""

    __tablename__ = "logins"
    __table_args__ = (
        check_in("outcome", LoginOutcome),
        Index("ix_logins_at", sa_text("at DESC")),
    )

    id: int | None = identity_pk()
    outcome: LoginOutcome = choice()
    credential_id: bytes | None = Field(default=None, sa_type=LargeBinary)
    session_id: uuid.UUID | None = Field(default=None, foreign_key="auth_sessions.session_id")
    ip: str | None = inet()
    user_agent: str | None = None
    at: datetime = now_default()


class PasskeyCredential(SQLModel, table=True):
    __tablename__ = "passkey_credentials"

    credential_id: bytes = Field(sa_type=LargeBinary, primary_key=True)
    public_key: bytes = Field(sa_type=LargeBinary)
    sign_count: int = Field(default=0, sa_type=BigInteger, sa_column_kwargs={"server_default": "0"})
    transports: list[str] = array(Text)
    label: str | None = None
    created_at: datetime = now_default()
    last_used_at: datetime | None = timestamptz(nullable=True)
    revoked_at: datetime | None = timestamptz(nullable=True)


class EnrollmentToken(SQLModel, table=True):
    """Recovery only via server console: the CLI prints a token, only its hash is stored."""

    __tablename__ = "enrollment_tokens"

    token_hash: bytes = Field(sa_type=LargeBinary, primary_key=True)
    expires_at: datetime = Field(sa_type=TIMESTAMPTZ)
    used_at: datetime | None = timestamptz(nullable=True)
    created_at: datetime = now_default()


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
    credential_id: bytes = Field(
        sa_type=LargeBinary, foreign_key="passkey_credentials.credential_id"
    )
    created_at: datetime = now_default()
    expires_at: datetime = Field(sa_type=TIMESTAMPTZ)
    last_seen_at: datetime = now_default()
    step_up_at: datetime | None = timestamptz(nullable=True)
    ip: str | None = inet()
    user_agent: str | None = None
    revoked_at: datetime | None = timestamptz(nullable=True)
