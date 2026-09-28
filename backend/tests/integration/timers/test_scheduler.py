"""F027: timers fire exactly once, in due order, survive restarts, and can be cancelled."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.db import create_session_factory
from app.markettime import FixedClock
from app.timers.models import Timer, TimerKind
from app.timers.service import TimerService
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import Factory

NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def service(factory: Factory, clock: FixedClock) -> TimerService:
    return TimerService(factory, alerts=AlertService(factory), clock=clock)


async def test_dedupe_and_cancel(factory: Factory, db_session: AsyncSession) -> None:
    timers = service(factory, FixedClock(NOW))
    assert await timers.schedule(TimerKind.CARD_EXPIRY, NOW, "k1")
    assert not await timers.schedule(TimerKind.CARD_EXPIRY, NOW, "k1")
    assert await db_session.scalar(select(func.count()).select_from(Timer)) == 1
    fired: list[str] = []

    async def handler(t: Timer) -> None:
        fired.append(t.dedupe_key)

    timers.register(TimerKind.CARD_EXPIRY, handler)
    assert await timers.cancel("k1")
    assert await timers.run_due() == 0 and fired == []


async def test_overdue_fire_in_order_after_restart_and_future_waits(factory: Factory) -> None:
    clock = FixedClock(NOW)
    before = service(factory, clock)  # scheduled by a process that then died
    for key, minutes in (("late", -1), ("early", -5), ("future", 10)):
        await before.schedule(TimerKind.CUTOFF_1, NOW + timedelta(minutes=minutes), key)

    fired: list[str] = []

    async def handler(t: Timer) -> None:
        fired.append(t.dedupe_key)

    restarted = service(factory, clock)
    restarted.register(TimerKind.CUTOFF_1, handler)
    assert await restarted.run_due() == 2
    assert fired == ["early", "late"]
    clock.advance(timedelta(minutes=10))
    await restarted.run_due()
    assert fired == ["early", "late", "future"]


async def test_unregistered_kinds_are_not_consumed(
    factory: Factory, db_session: AsyncSession
) -> None:
    timers = service(factory, FixedClock(NOW))
    await timers.schedule(TimerKind.CARD_EXPIRY, NOW, "wait-for-f036")

    async def handler(t: Timer) -> None:
        raise AssertionError("wrong kind fired")

    timers.register(TimerKind.CUTOFF_2, handler)
    assert await timers.run_due() == 0
    status = await db_session.scalar(select(Timer.status))
    assert status == "scheduled"


async def test_racing_schedulers_fire_each_timer_once(factory: Factory) -> None:
    clock = FixedClock(NOW)
    calls: list[str] = []

    async def handler(t: Timer) -> None:
        calls.append(t.dedupe_key)
        await asyncio.sleep(0.01)

    a, b = service(factory, clock), service(factory, clock)
    for s in (a, b):
        s.register(TimerKind.ENTRY_CANCEL, handler)
    for i in range(10):
        await a.schedule(TimerKind.ENTRY_CANCEL, NOW - timedelta(seconds=i), f"t{i}")
    await asyncio.gather(a.run_due(), b.run_due())
    assert sorted(calls) == sorted(f"t{i}" for i in range(10))


async def test_failure_alerts_and_retries_once(factory: Factory, db_session: AsyncSession) -> None:
    clock = FixedClock(NOW)
    timers = service(factory, clock)
    attempts: list[str] = []

    async def handler(t: Timer) -> None:
        attempts.append(t.dedupe_key)
        raise RuntimeError("boom")

    timers.register(TimerKind.CUTOFF_2, handler)
    await timers.schedule(TimerKind.CUTOFF_2, NOW, "c2")
    await timers.run_due()
    clock.advance(timedelta(seconds=5))
    await timers.run_due()
    clock.advance(timedelta(minutes=1))
    await timers.run_due()
    assert attempts == ["c2", "c2:retry"]  # retried once, then alert only
    kinds = (
        await db_session.scalars(select(Alert.kind).where(col(Alert.kind) == "timer_failed"))
    ).all()
    assert len(kinds) == 2


async def test_run_loop_wakes_on_schedule(factory: Factory) -> None:
    timers = service(factory, FixedClock(NOW))
    done = asyncio.Event()

    async def handler(t: Timer) -> None:
        done.set()

    timers.register(TimerKind.OFFHOURS_RELEASE, handler)
    task = asyncio.create_task(timers.run())
    await timers.schedule(TimerKind.OFFHOURS_RELEASE, NOW, "release")
    async with asyncio.timeout(3):
        await done.wait()
    task.cancel()
