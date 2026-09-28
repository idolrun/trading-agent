"""In-memory channel histories for tests and dev."""

from typing import Any

from app.messageapi.port import MessageHistoryUnavailable


class FakeMessageHistory:
    def __init__(self) -> None:
        self.channels: dict[int, list[dict[str, Any]]] = {}
        self.unavailable = False
        self.calls: list[tuple[int, int | None, int]] = []

    def add(self, *payloads: dict[str, Any]) -> None:
        for payload in payloads:
            channel = int(payload["message"]["channel_id"])
            self.channels.setdefault(channel, []).append(payload)
            self.channels[channel].sort(key=lambda p: int(p["message"]["id"]))

    async def fetch_after(
        self, channel_id: int, after_message_id: int | None, limit: int
    ) -> list[dict[str, Any]]:
        self.calls.append((channel_id, after_message_id, limit))
        if self.unavailable:
            raise MessageHistoryUnavailable("fake history down")
        after = after_message_id or 0
        newer = [p for p in self.channels.get(channel_id, []) if int(p["message"]["id"]) > after]
        return newer[:limit]
