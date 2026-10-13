"""F087: the provider limiter follows the live concurrency config without a restart."""

import asyncio

from app.llm.limiter import DEFAULT_CONCURRENCY, ProviderLimiter


async def _occupy(limiter: ProviderLimiter, n: int, release: asyncio.Event) -> list[asyncio.Task]:
    async def hold() -> None:
        async with limiter.slot("typesafe"):
            await release.wait()

    tasks = [asyncio.create_task(hold()) for _ in range(n)]
    await asyncio.sleep(0)
    return tasks


async def _can_enter(limiter: ProviderLimiter) -> bool:
    try:
        async with asyncio.timeout(0.05):
            async with limiter.slot("typesafe"):
                return True
    except TimeoutError:
        return False


async def test_unset_size_is_the_default() -> None:
    limiter = ProviderLimiter(lambda _: None)
    release = asyncio.Event()
    tasks = await _occupy(limiter, DEFAULT_CONCURRENCY, release)
    assert not await _can_enter(limiter)
    release.set()
    await asyncio.gather(*tasks)


async def test_a_bigger_size_applies_on_the_next_call() -> None:
    size = {"typesafe": 2}
    limiter = ProviderLimiter(size.get)
    release = asyncio.Event()
    tasks = await _occupy(limiter, 2, release)
    assert not await _can_enter(limiter)
    size["typesafe"] = 6
    assert await _can_enter(limiter)
    release.set()
    await asyncio.gather(*tasks)
