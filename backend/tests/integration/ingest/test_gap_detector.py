"""F014: gap detection and backfill (AC-21 on fakes)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.db import create_session_factory, in_transaction
from app.dispatcher.service import AlertPoisonHandler, Dispatcher
from app.dispatcher.stub import StubProcessor
from app.inbox import repo as inbox
from app.inbox.models import InboxItem, MessageEvent
from app.ingest.adapter import to_envelope
from app.ingest.gaps import GapDetector
from app.markettime import FixedClock
from app.messageapi.fake import FakeMessageHistory
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from tests.integration.conftest import _url
from tests.messages import CHANNEL, create, snowflake

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def config(factory: Factory) -> ConfigService:
    service = ConfigService(factory)
    await service.update(
        RuntimeConfig(channels=[CHANNEL]), expected_version=0, actor=Actor.CLI, reason="t"
    )
    return service


def messages(n: int, start: datetime = T0) -> list[dict[str, Any]]:
    return [create(start + timedelta(seconds=10 * i), f"message {i + 1}") for i in range(n)]


async def deliver(factory: Factory, *payloads: dict[str, Any]) -> None:
    for payload in payloads:
        async with in_transaction(factory, None) as db:
            await inbox.accept(db, to_envelope(payload, T0))


def detector(
    factory: Factory, config: ConfigService, history: FakeMessageHistory, now: datetime
) -> GapDetector:
    return GapDetector(
        factory,
        history=history,
        config=config,
        alerts=AlertService(factory),
        clock=FixedClock(now),
        page_size=3,  # force paging
    )


async def backfilled_ids(db: AsyncSession) -> list[int]:
    rows = await db.scalars(
        select(MessageEvent.message_id)
        .where(MessageEvent.backfilled)
        .order_by(MessageEvent.message_id)
    )
    return list(rows.all())


async def test_outage_gap_is_backfilled_once_and_processed(
    factory: Factory, config: ConfigService, db_session: AsyncSession
) -> None:
    msgs = messages(10)
    history = FakeMessageHistory()
    history.add(*msgs)
    await deliver(factory, *msgs[:3], *msgs[7:])  # webhook missed 4-7
    gaps = detector(factory, config, history, T0 + timedelta(hours=1))

    assert await gaps.run_once() == 4
    assert await backfilled_ids(db_session) == [int(m["message"]["id"]) for m in msgs[3:7]]
    assert await gaps.run_once() == 0  # nothing new, no duplicates

    stub = StubProcessor(factory)
    dispatcher = Dispatcher(
        factory,
        config=config,
        processor=stub,
        poison=AlertPoisonHandler(AlertService(factory)),
        clock=FixedClock(T0 + timedelta(hours=1)),
    )
    await dispatcher.start()
    async with asyncio.timeout(5):
        while len(stub.calls) < 10:  # noqa: ASYNC110 — waiting on worker tasks
            await asyncio.sleep(0.05)
    await dispatcher.stop()
    rows = (await db_session.scalars(select(InboxItem))).all()
    assert len(rows) == 10 and {r.status for r in rows} == {"stale"}


async def test_gap_during_runtime_before_newer_messages(
    factory: Factory, config: ConfigService, db_session: AsyncSession
) -> None:
    msgs = messages(10)
    history = FakeMessageHistory()
    history.add(*msgs[:3])
    await deliver(factory, *msgs[:3])
    gaps = detector(factory, config, history, T0 + timedelta(hours=1))
    assert await gaps.run_once() == 0  # verified up to message 3

    history.add(*msgs[3:])  # outage: webhook misses 4-7, then delivers 8-10
    await deliver(factory, *msgs[7:])
    assert await gaps.run_once() == 4  # found although newer messages were already stored
    assert len(await backfilled_ids(db_session)) == 4


async def test_restart_reverifies_only_the_lookback_window(
    factory: Factory, config: ConfigService
) -> None:
    now = T0 + timedelta(hours=10)
    old = messages(2, start=T0)  # older than the 8 h lookback
    recent = messages(2, start=now - timedelta(minutes=5))
    history = FakeMessageHistory()
    history.add(*old, *recent)
    await deliver(factory, *old, *recent)
    gaps = detector(factory, config, history, now)
    assert await gaps.run_once() == 0
    assert history.calls[0] == (CHANNEL, int(old[-1]["message"]["id"]), 3)


async def test_new_channel_backfills_only_the_lookback_window(
    factory: Factory, config: ConfigService, db_session: AsyncSession
) -> None:
    """A channel with nothing stored must not page its whole history (2026-10-12 flood)."""
    now = T0 + timedelta(days=30)
    old = messages(3, start=T0)  # years of history in production
    recent = messages(2, start=now - timedelta(hours=1))
    history = FakeMessageHistory()
    history.add(*old, *recent)
    gaps = detector(factory, config, history, now)
    assert await gaps.run_once() == 2
    assert history.calls[0] == (CHANNEL, snowflake(now - timedelta(hours=8)), 3)
    assert await backfilled_ids(db_session) == [int(m["message"]["id"]) for m in recent]


async def test_history_unavailable_warns_and_retries(
    factory: Factory, config: ConfigService, db_session: AsyncSession
) -> None:
    history = FakeMessageHistory()
    history.add(*messages(2))
    history.unavailable = True
    gaps = detector(factory, config, history, T0 + timedelta(hours=1))
    assert await gaps.run_once() == 0
    assert await gaps.run_once() == 0  # same hour: one alert
    alerts = (await db_session.scalars(select(Alert))).all()
    assert [(a.kind, a.severity) for a in alerts] == [("message_api", "warning")]
    history.unavailable = False
    assert await gaps.run_once() == 2


async def test_unmappable_history_payload_is_alerted_and_skipped(
    factory: Factory, config: ConfigService, db_session: AsyncSession
) -> None:
    good = messages(2)
    bad = create(T0 + timedelta(seconds=5), "x")
    bad["message"]["author"] = {}  # no author id
    history = FakeMessageHistory()
    history.add(*good, bad)
    gaps = detector(factory, config, history, T0 + timedelta(hours=1))
    assert await gaps.run_once() == 2
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and (alert.kind, alert.severity) == ("message_api", "urgent")
