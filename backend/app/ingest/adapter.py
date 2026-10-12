"""PROVISIONAL Message API payload -> `Envelope` (pure). The only file F050 replaces (OD-1/OD-2).

Provisional JSON:
    {"type": "message.create" | "message.update" | "message.delete",
     "message": {"id", "channel_id", "thread_id", "guild_id", "author": {"id", "name"?}, "content",
                 "timestamp",
                 "edited_timestamp", "deleted_timestamp", "reply_to_id", "mentions": [ids]}}
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, PositiveInt, ValidationError

from app.inbox.envelope import Envelope, EventKey
from app.inbox.models import EventType
from app.markettime import snowflake_to_datetime


class UnmappablePayload(ValueError):
    """The payload does not match the (provisional) Message API contract."""


class _Author(BaseModel):
    id: PositiveInt
    name: str | None = None  # display only; kept in the raw payload, read back for card labels


class _Message(BaseModel):
    id: PositiveInt
    channel_id: PositiveInt  # the context channel: a thread message carries its parent's id
    thread_id: PositiveInt | None = None
    guild_id: PositiveInt | None = None
    author: _Author
    content: str | None = None
    edited_timestamp: datetime | None = None
    deleted_timestamp: datetime | None = None
    reply_to_id: PositiveInt | None = None
    mentions: list[PositiveInt] = []


class _Event(BaseModel):
    type: Literal["message.create", "message.update", "message.delete"]
    message: _Message


def to_envelope(payload: dict[str, Any], received_at: datetime) -> Envelope:
    """Map one webhook/history payload. Raises `UnmappablePayload`.

    posted_at: create = snowflake time; edit/delete = event time, so a correction is sequenced
    after the messages posted before it (OD-24 proposal). Edit event_seq = edit time in ms
    (C-EVSEQ, provisional until OD-2).
    """
    try:
        event = _Event.model_validate(payload)
    except ValidationError as exc:
        raise UnmappablePayload(str(exc)) from exc
    msg = event.message
    text = msg.content
    edited_at = None
    match event.type:
        case "message.create":
            if text is None:
                raise UnmappablePayload("create without content")
            key = EventKey(msg.id, EventType.CREATE, 0)
            posted_at = snowflake_to_datetime(msg.id)
        case "message.update":
            if text is None or msg.edited_timestamp is None:
                raise UnmappablePayload("update needs content and edited_timestamp")
            edited_at = posted_at = msg.edited_timestamp
            key = EventKey(msg.id, EventType.EDIT, int(edited_at.timestamp() * 1000))
        case "message.delete":
            text = None
            posted_at = msg.deleted_timestamp or received_at
            key = EventKey(msg.id, EventType.DELETE, 0)
    if posted_at.tzinfo is None:
        raise UnmappablePayload("timestamps must carry a timezone")
    return Envelope(
        key=key,
        provider_id=msg.author.id,
        channel_id=msg.channel_id,
        thread_id=msg.thread_id,
        guild_id=msg.guild_id,
        posted_at=posted_at,
        received_at=received_at,
        reply_to_id=msg.reply_to_id,
        mentioned_author_ids=tuple(msg.mentions),
        text=text,
        edited_at=edited_at,
        payload=payload,
    )
