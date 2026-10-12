"""Discord message -> provisional Message API payload (`app.ingest.adapter.to_envelope`).

Pure: every function reads only the attributes it needs, so tests pass plain namespaces.
"""

from datetime import datetime
from typing import Any

import discord

# Signal bots often post the whole alert as an embed; the pipeline is text-only, so the embed
# text is appended to the content (the interpreter's literal spans are checked against it).
MAX_EMBEDS = 5


def message_text(message: discord.Message) -> str:
    parts = [message.content or ""]
    for embed in (message.embeds or [])[:MAX_EMBEDS]:
        parts += [text for text in (embed.title, embed.description) if text]
    return "\n".join(part for part in parts if part)


def context_channel(channel: Any) -> tuple[int, int | None]:
    """(channel the message belongs to, thread id). A thread message belongs to its parent
    channel's context and keeps its thread id (only `discord.Thread` has `parent_id`)."""
    parent = getattr(channel, "parent_id", None)
    return (parent, channel.id) if parent else (channel.id, None)


def _author(user: Any) -> dict[str, Any]:
    """Id plus the name shown in Discord (server nickname, else global name, else username), so the
    desk can label a provider; the id alone stays the identity."""
    name = getattr(user, "display_name", None) or getattr(user, "name", None)
    return {"id": user.id, "name": name} if isinstance(name, str) and name else {"id": user.id}


def _body(message: discord.Message, **extra: Any) -> dict[str, Any]:
    reference = message.reference
    channel_id, thread_id = context_channel(message.channel)
    guild = getattr(message, "guild", None)
    return {
        "id": message.id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "guild_id": guild.id if guild is not None else None,
        "author": _author(message.author),
        "content": message_text(message),
        "reply_to_id": reference.message_id if reference else None,
        "mentions": [user.id for user in message.mentions],
        **extra,
    }


def create_payload(message: discord.Message) -> dict[str, Any] | None:
    """None = not ours to forward (bot author, as in the original bridge)."""
    if message.author.bot:
        return None
    return {"type": "message.create", "message": _body(message)}


def edit_payload(message: discord.Message) -> dict[str, Any] | None:
    """None = bot author, or an update that is not a text edit (embed unfurl, pin)."""
    if message.author.bot or message.edited_at is None:
        return None
    return {
        "type": "message.update",
        "message": _body(message, edited_timestamp=message.edited_at.isoformat()),
    }


def delete_payload(
    message_id: int,
    channel_id: int,
    author_id: int,
    deleted_at: datetime,
    thread_id: int | None = None,
) -> dict[str, Any]:
    return {
        "type": "message.delete",
        "message": {
            "id": message_id,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "author": {"id": author_id},
            "deleted_timestamp": deleted_at.isoformat(),
        },
    }
