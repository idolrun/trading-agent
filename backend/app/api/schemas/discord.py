from datetime import datetime
from typing import Literal

from app.api.schemas.base import CamelModel, DiscordId


class DiscordChannel(CamelModel):
    id: DiscordId
    name: str | None  # None = the account cannot see the channel
    guild: str | None
    visible: bool


class DiscordStatus(CamelModel):
    state: Literal["not_configured", "connecting", "connected", "login_failed"]
    account: str | None
    last_message_at: datetime | None
    channels: list[DiscordChannel]
