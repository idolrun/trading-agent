"""In-process sliding-window rate limiter (single process; no Redis, TRD §15.2)."""

from collections import defaultdict, deque
from datetime import timedelta

from app.markettime import Clock


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: timedelta, clock: Clock) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Record an attempt for `key`; False once `limit` attempts fall inside the window."""
        now = self._clock.now().timestamp()
        hits = self._hits[key]
        while hits and hits[0] <= now - self._window.total_seconds():
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
