"""System tables (database-schema.md §3.4, §5)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, SmallInteger
from sqlmodel import Field, SQLModel

from app.db import TIMESTAMPTZ


class DispatcherHeartbeat(SQLModel, table=True):
    """Current state: single row; `/health` fails when `now() - beat_at > 15 s`."""

    __tablename__ = "dispatcher_heartbeat"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: int = Field(default=1, sa_type=SmallInteger, primary_key=True)
    beat_at: datetime = Field(sa_type=TIMESTAMPTZ)
    host: str
    pid: int
