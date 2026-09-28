"""Manual correction audit (database-schema.md §5, C73)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.db import check_in, choice, identity_pk, jsonb, now_default


class CorrectionTarget(StrEnum):
    PROVIDER_POSITION = "provider_position"
    MESSAGE_LINK = "message_link"
    PROVIDER_PROFILE = "provider_profile"


class ManualCorrection(SQLModel, table=True):
    """Immutable audit: user corrections (fed into evaluation data)."""

    __tablename__ = "manual_corrections"
    __table_args__ = (
        check_in("target_type", CorrectionTarget),
        Index("ix_manual_corrections_target", "target_type", "target_id"),
    )

    id: int | None = identity_pk()
    target_type: CorrectionTarget = choice()
    target_id: str
    action: str
    before: dict[str, Any] | None = jsonb(nullable=True)
    after: dict[str, Any] = jsonb()
    reason: str | None = None
    session_id: uuid.UUID = Field(foreign_key="auth_sessions.session_id")
    at: datetime = now_default()
