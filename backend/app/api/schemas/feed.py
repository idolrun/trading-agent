import uuid
from datetime import datetime

from app.api.schemas.base import CamelModel, DiscordId


class FeedItem(CamelModel):
    """Every message event, nothing hidden (FR-10, FR-1c): labels explain what happened."""

    message_id: DiscordId
    event_type: str
    event_seq: int
    provider_id: DiscordId
    channel_id: DiscordId
    posted_at: datetime
    text: str | None
    reply_to_id: DiscordId | None
    status: str | None  # inbox status; None = not queued (never happens for accepted events)
    labels: list[
        str
    ]  # held_until_open, stale, late, backfilled, muted, channel_not_configured, failed
    msg_types: list[str]
    card_ids: list[uuid.UUID]
