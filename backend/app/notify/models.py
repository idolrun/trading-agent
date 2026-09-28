"""Web Push subscriptions (database-schema.md §5, FR-26)."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.db import now_default, timestamptz


class PushSubscription(SQLModel, table=True):
    __tablename__ = "push_subscriptions"

    subscription_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    endpoint: str = Field(unique=True)
    p256dh: str
    auth: str
    label: str | None = None
    created_at: datetime = now_default()
    revoked_at: datetime | None = timestamptz(nullable=True)
