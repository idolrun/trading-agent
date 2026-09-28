"""Transport types: one message event as stored in `message_events` / `inbox`."""

from datetime import datetime
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict

from app.inbox.models import EventType


class EventKey(NamedTuple):
    """Transport idempotency key (database-schema.md §6)."""

    message_id: int
    event_type: EventType
    event_seq: int


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: EventKey
    provider_id: int  # Discord author ID
    channel_id: int
    posted_at: datetime  # sequencing / validity clock origin (C90, OD-24)
    received_at: datetime
    reply_to_id: int | None = None
    mentioned_author_ids: tuple[int, ...] = ()
    text: str | None = None  # None only for deletes
    edited_at: datetime | None = None
    backfilled: bool = False
    payload: dict[str, Any]  # raw envelope as received (audit)
