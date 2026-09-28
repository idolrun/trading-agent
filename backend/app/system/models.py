"""System tables (database-schema.md §3.4, §5)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, SmallInteger
from sqlmodel import Field, SQLModel

from app.db import TIMESTAMPTZ, check_in, choice, identity_pk, now_default, timestamptz
from app.db import flag as flag_column
from app.runtime_config.models import Actor


class DispatcherHeartbeat(SQLModel, table=True):
    """Current state: single row; `/health` fails when `now() - beat_at > 15 s`."""

    __tablename__ = "dispatcher_heartbeat"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: int = Field(default=1, sa_type=SmallInteger, primary_key=True)
    beat_at: datetime = Field(sa_type=TIMESTAMPTZ)
    host: str
    pid: int


class FlagName(StrEnum):
    ENTRIES_BLOCKED = "entries_blocked"
    KILL_SWITCH = "kill_switch"
    CATCH_UP = "catch_up"
    FEED_DEGRADED = "feed_degraded"
    PARSER_DEGRADED = "parser_degraded"


class SystemFlag(SQLModel, table=True):
    """Current state: one row per flag, seeded by migration 0003."""

    __tablename__ = "system_flags"
    __table_args__ = (check_in("flag", FlagName),)

    flag: FlagName = choice(primary_key=True)
    active: bool = flag_column()
    reason: str | None = None
    since: datetime | None = timestamptz(nullable=True)
    updated_at: datetime = now_default()


class SystemFlagEvent(SQLModel, table=True):
    """Immutable audit: every flag change."""

    __tablename__ = "system_flag_events"
    __table_args__ = (check_in("actor", Actor),)

    id: int | None = identity_pk()
    flag: FlagName = choice(foreign_key="system_flags.flag")
    active: bool
    reason: str | None = None
    actor: Actor = choice()
    session_id: uuid.UUID | None = Field(default=None, foreign_key="auth_sessions.session_id")
    at: datetime = now_default()
