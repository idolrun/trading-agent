"""In-process TTL cache for slow-changing market data (chains, optionable list; TRD §15.2)."""

from collections.abc import Awaitable, Callable, Hashable
from datetime import datetime, timedelta

from app.markettime import Clock


class TtlCache[K: Hashable, V]:
    """ponytail: concurrent misses on one key may load twice; add a per-key lock if costly."""

    def __init__(self, ttl: timedelta, clock: Clock) -> None:
        self._ttl = ttl
        self._clock = clock
        self._entries: dict[K, tuple[datetime, V]] = {}

    async def get(self, key: K, load: Callable[[], Awaitable[V]]) -> V:
        now = self._clock.now()
        hit = self._entries.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
        value = await load()
        self._entries[key] = (now + self._ttl, value)
        return value

    def invalidate(self, key: K | None = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)
