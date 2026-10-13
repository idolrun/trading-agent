"""F013 with a fixed clock: AC-8 feed silence, catch-up mode."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.db import create_session_factory, in_transaction
from app.inbox import repo as inbox
from app.inbox.models import InboxItem
from app.ingest.adapter import to_envelope
from app.ingest.feed_health import FeedHealthMonitor
from app.markettime import FixedClock
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName
from tests.integration.conftest import _url
from tests.messages import create

MARKET = datetime(2026, 9, 28, 19, 0, tzinfo=UTC)  # Monday 15:00 ET
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def monitor(
    factory: Factory, at: datetime, **config: Any
) -> tuple[FeedHealthMonitor, FlagService, FixedClock]:
    service = ConfigService(factory)
    await service.update(RuntimeConfig(**config), expected_version=0, actor=Actor.CLI, reason="t")
    alerts = AlertService(factory)
    flags = FlagService(factory, alerts)
    clock = FixedClock(at)
    return (
        FeedHealthMonitor(factory, config=service, flags=flags, alerts=alerts, clock=clock),
        flags,
        clock,
    )


async def test_ac8_silence_degrades_and_heartbeat_recovers(
    factory: Factory, db_session: AsyncSession
) -> None:
    mon, flags, clock = await monitor(factory, MARKET, feed_silence_s=60, catch_up_backlog_n=20)
    clock.advance(timedelta(seconds=60))
    await mon.tick()
    assert not await flags.is_active(FlagName.FEED_DEGRADED)

    clock.advance(timedelta(seconds=1))
    await mon.tick()
    await mon.tick()  # still silent: no second alert
    assert await flags.is_active(FlagName.FEED_DEGRADED)
    alerts = (await db_session.scalars(select(Alert))).all()
    assert [(a.kind, a.severity) for a in alerts] == [("system_flag", "urgent")]

    mon.note_heartbeat(clock.now())
    await mon.tick()
    assert not await flags.is_active(FlagName.FEED_DEGRADED)


async def test_quiet_after_close_is_not_degraded(factory: Factory) -> None:
    after_close = datetime(2026, 9, 28, 20, 30, tzinfo=UTC)  # 16:30 ET
    mon, flags, clock = await monitor(factory, after_close, feed_silence_s=60)
    clock.advance(timedelta(hours=3))
    await mon.tick()
    assert not await flags.is_active(FlagName.FEED_DEGRADED)


async def test_catch_up_until_backlog_drains(factory: Factory) -> None:
    mon, flags, clock = await monitor(factory, MARKET, feed_silence_s=600, catch_up_backlog_n=20)
    for i in range(50):
        payload = create(clock.now() - timedelta(seconds=1), f"msg {i}", seq=i)
        async with in_transaction(factory, None) as db:
            await inbox.accept(db, to_envelope(payload, clock.now()))
    await mon.tick()
    assert await flags.is_active(FlagName.CATCH_UP)
    async with in_transaction(factory, None) as db:
        await db.execute(update(InboxItem).values(status="done"))
    await mon.tick()
    assert not await flags.is_active(FlagName.CATCH_UP)


async def test_unconfigured_thresholds_warn_once(
    factory: Factory, db_session: AsyncSession
) -> None:
    mon, flags, clock = await monitor(factory, MARKET)
    clock.advance(timedelta(hours=1))
    await mon.tick()
    await mon.tick()
    alerts = (await db_session.scalars(select(Alert).order_by(Alert.kind))).all()
    assert [(a.kind, a.severity) for a in alerts] == [
        ("catch_up", "warning"),
        ("feed_silence", "warning"),
    ]
    assert not await flags.is_active(FlagName.FEED_DEGRADED)
