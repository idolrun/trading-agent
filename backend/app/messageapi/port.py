"""Message history (backfill source, FR-1d). Real client: F050 (OD-1/OD-2)."""

from typing import Any, Protocol


class MessageHistoryUnavailable(Exception):
    pass


class MessageHistory(Protocol):
    async def fetch_after(
        self, channel_id: int, after_message_id: int | None, limit: int
    ) -> list[dict[str, Any]]:
        """Raw provisional payloads (same shape as the webhook), oldest first.
        `after_message_id=None` = from the start of the channel."""
        ...
