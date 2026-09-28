"""Live event fan-out (TRD §15.2): one LISTEN connection -> a bounded queue per subscriber.
Payloads are ids only; clients refetch through the read API (F030)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.db import listen_forever

logger = logging.getLogger(__name__)
CHANNELS = ("cards", "alerts", "flags", "orders", "fills", "positions", "lots", "feed")
QUEUE_BOUND = 1000
# Channels whose NOTIFY payload predates the JSON form (a bare id / flag name).
LEGACY = {"alerts": "alert.created", "flags": "flag.changed"}


@dataclass(frozen=True)
class Event:
    type: str  # card.created, card.updated, alert.created, flag.changed, order.updated, ...
    id: str


def to_event(channel: str, payload: str) -> Event | None:
    try:
        data = json.loads(payload)
        return Event(type=str(data["type"]), id=str(data["id"]))
    except (ValueError, KeyError, TypeError):
        if channel in LEGACY:
            return Event(type=LEGACY[channel], id=payload)
    logger.warning("unreadable notify payload", extra={"channel": channel})
    return None


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event | None]] = set()

    async def run(self, dsn: str) -> None:
        """Lifespan task; reconnects forever (listen_forever)."""
        await listen_forever(dsn, CHANNELS, self._on_notify, self._on_connect)

    def publish(self, event: Event) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # slow client: drop it; it reconnects and refetches
                self._subscribers.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    async def stream(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event | None] = asyncio.Queue(QUEUE_BOUND)
        self._subscribers.add(queue)
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def _on_notify(self, channel: str, payload: str) -> None:
        if (event := to_event(channel, payload)) is not None:
            self.publish(event)

    async def _on_connect(self) -> None:
        # Events missed while disconnected are not replayed: tell clients to refetch.
        self.publish(Event(type="resync", id=""))
