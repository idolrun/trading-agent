"""`MessageHistory` over the Discord client: gap backfill after downtime or a dropped session."""

from typing import TYPE_CHECKING, Any

import discord

from app.discord_source import mapping
from app.messageapi.port import MessageHistoryUnavailable

if TYPE_CHECKING:
    from app.discord_source.listener import DiscordListener


READY_WAIT_S = 10.0


class DiscordHistory:
    def __init__(self, listener: "DiscordListener") -> None:
        self._listener = listener

    async def fetch_after(
        self, channel_id: int, after_message_id: int | None, limit: int
    ) -> list[dict[str, Any]]:
        client = await self._listener.ready_client(READY_WAIT_S)
        if client is None:
            raise MessageHistoryUnavailable("discord not connected")
        channel = client.get_channel(channel_id)
        if channel is None or not hasattr(channel, "history"):
            raise MessageHistoryUnavailable(f"channel {channel_id} not visible to the account")
        after = discord.Object(after_message_id) if after_message_id else None
        try:
            messages = [
                m async for m in channel.history(limit=limit, after=after, oldest_first=True)
            ]
        except discord.HTTPException as exc:
            raise MessageHistoryUnavailable(str(exc)) from exc
        # ponytail: threads under the channel are not paged (active threads would need
        # `channel.threads` + each thread's history); a missed thread message is not backfilled.
        # ponytail: bot messages are dropped after paging, so a full page of only bot posts ends
        # the backfill early; fetch by page cursor here if a channel is mostly bots.
        payloads = (mapping.create_payload(m) for m in messages)
        return [p for p in payloads if p is not None]
