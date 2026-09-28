"""Runtime config storage and audit (database-schema.md §5). Schema of `document` is F006."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, SmallInteger
from sqlmodel import Field, SQLModel

from app.db import check_in, choice, identity_pk, jsonb, now_default


class Actor(StrEnum):
    USER = "user"
    CLI = "cli"
    SYSTEM = "system"


class ConfigRow(SQLModel, table=True):
    """Current state: the single live `RuntimeConfig` document."""

    __tablename__ = "config"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: int = Field(default=1, sa_type=SmallInteger, primary_key=True)
    version: int
    document: dict[str, Any] = jsonb()
    updated_at: datetime = now_default()


class ConfigChange(SQLModel, table=True):
    """Immutable audit: every config version."""

    __tablename__ = "config_changes"
    __table_args__ = (check_in("actor", Actor),)

    id: int | None = identity_pk()
    version: int = Field(unique=True)
    document: dict[str, Any] = jsonb()
    diff: dict[str, Any] = jsonb()
    session_id: uuid.UUID | None = Field(default=None, foreign_key="auth_sessions.session_id")
    actor: Actor = choice()
    reason: str | None = None
    at: datetime = now_default()
