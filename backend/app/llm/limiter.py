"""One semaphore per provider (TRD §2.4). The wait is reported as queue wait, not model latency."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

DEFAULT_CONCURRENCY = 4


class ProviderLimiter:
    def __init__(self, concurrency: Callable[[str], int | None]) -> None:
        self._concurrency = concurrency
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    @asynccontextmanager
    async def slot(self, provider: str) -> AsyncIterator[int]:
        """Hold one of `provider`'s slots; yields the queue wait in ms."""
        # ponytail: size is read on first use; a config change needs a restart to resize.
        sem = self._semaphores.get(provider)
        if sem is None:
            sem = asyncio.Semaphore(self._concurrency(provider) or DEFAULT_CONCURRENCY)
            self._semaphores[provider] = sem
        started = time.monotonic()
        async with sem:
            yield int((time.monotonic() - started) * 1000)
