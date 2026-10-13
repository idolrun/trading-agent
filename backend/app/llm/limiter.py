"""One semaphore per provider (TRD §2.4). The wait is reported as queue wait, not model latency."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from app.observability.counters import bump

DEFAULT_CONCURRENCY = 4


class ProviderLimiter:
    def __init__(self, concurrency: Callable[[str], int | None]) -> None:
        self._concurrency = concurrency
        self._semaphores: dict[str, tuple[int, asyncio.Semaphore]] = {}

    @asynccontextmanager
    async def slot(self, provider: str) -> AsyncIterator[int]:
        """Hold one of `provider`'s slots; yields the queue wait in ms. The size follows the
        live config (F087): a change takes effect on the next call, no restart."""
        size = self._concurrency(provider) or DEFAULT_CONCURRENCY
        held = self._semaphores.get(provider)
        if held is None or held[0] != size:
            # ponytail: callers already inside finish on the old semaphore, so for a moment up
            # to old + new calls run; fine for a config change, not for a hard vendor cap.
            held = (size, asyncio.Semaphore(size))
            self._semaphores[provider] = held
        sem = held[1]
        started = time.monotonic()
        async with sem:
            wait_ms = int((time.monotonic() - started) * 1000)
            bump(f"slot:{provider}")
            if wait_ms:
                bump(f"slot_waited:{provider}")
            yield wait_ms
