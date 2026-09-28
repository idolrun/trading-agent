import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory
from app.main import libpq_dsn
from app.runtime_config.models import Actor, ConfigChange
from app.runtime_config.repo import VersionConflict
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from tests.integration.conftest import _url

Factory = Callable[[], ConfigService]


async def eventually(predicate: Callable[[], bool]) -> None:
    """Poll another service's cache (it is refreshed by its own LISTEN task)."""
    async with asyncio.timeout(5):
        while not predicate():  # noqa: ASYNC110 — nothing to await on; the cache is plain state
            await asyncio.sleep(0.05)


@pytest.fixture
async def service_factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield lambda: ConfigService(
        create_session_factory(engine), libpq_dsn(_url("test_database_url"))
    )
    await engine.dispose()


async def test_update_writes_version_and_audit_row(
    service_factory: Factory, db_session: AsyncSession
) -> None:
    service = service_factory()
    await service.load()
    assert service.get() is None

    v1, _ = await service.update(
        RuntimeConfig(), expected_version=0, actor=Actor.CLI, reason="bootstrap"
    )
    changed = RuntimeConfig(feed_silence_s=600, trading_mode="paper")
    v2, config = await service.update(changed, expected_version=v1, actor=Actor.USER, reason="tune")
    assert (v1, v2) == (1, 2)
    assert service.get() == config

    rows = (await db_session.scalars(select(ConfigChange).order_by(ConfigChange.version))).all()
    assert [(r.version, r.actor, r.reason) for r in rows] == [
        (1, "cli", "bootstrap"),
        (2, "user", "tune"),
    ]
    assert rows[1].diff == {
        "feed_silence_s": {"before": None, "after": 600},
        "trading_mode": {"before": "shadow", "after": "paper"},
    }


async def test_stale_expected_version_conflicts(service_factory: Factory) -> None:
    service = service_factory()
    await service.update(RuntimeConfig(), expected_version=0, actor=Actor.CLI, reason="a")
    for stale in (0, 5):
        with pytest.raises(VersionConflict):
            await service.update(
                RuntimeConfig(), expected_version=stale, actor=Actor.CLI, reason="b"
            )


async def test_concurrent_updates_cannot_both_win(service_factory: Factory) -> None:
    a, b = service_factory(), service_factory()
    await a.update(RuntimeConfig(), expected_version=0, actor=Actor.CLI, reason="init")
    results = await asyncio.gather(
        a.update(RuntimeConfig(feed_silence_s=1), expected_version=1, actor=Actor.USER, reason="a"),
        b.update(RuntimeConfig(feed_silence_s=2), expected_version=1, actor=Actor.USER, reason="b"),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["VersionConflict", "tuple"]


async def test_listener_refreshes_other_process_cache(service_factory: Factory) -> None:
    writer, reader = service_factory(), service_factory()
    listener = asyncio.create_task(reader.listen())
    try:
        await writer.update(
            RuntimeConfig(feed_silence_s=42), expected_version=0, actor=Actor.CLI, reason="x"
        )
        await eventually(lambda: (c := reader.get()) is not None and c.feed_silence_s == 42)
        await writer.update(
            RuntimeConfig(feed_silence_s=43), expected_version=1, actor=Actor.CLI, reason="y"
        )
        await eventually(lambda: (c := reader.get()) is not None and c.feed_silence_s == 43)
    finally:
        listener.cancel()
