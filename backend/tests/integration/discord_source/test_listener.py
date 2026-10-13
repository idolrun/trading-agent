"""Discord listener -> inbox."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.service import AlertService
from app.db import create_session_factory
from app.discord_source import mapping
from app.discord_source.listener import DiscordListener
from app.inbox.models import EventType, InboxItem, MessageEvent
from app.markettime import FixedClock
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from tests.integration.conftest import _url
from tests.messages import CHANNEL, PROVIDER_A, snowflake

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
OTHER_CHANNEL = CHANNEL + 1
Factory = async_sessionmaker[AsyncSession]


class FeedSpy:
    def __init__(self) -> None:
        self.messages: list[datetime] = []
        self.heartbeats: list[datetime] = []

    def note_message(self, at: datetime) -> None:
        self.messages.append(at)

    def note_heartbeat(self, at: datetime) -> None:
        self.heartbeats.append(at)


def message(at: datetime, content: str, channel: int = CHANNEL, **over: Any) -> NS:
    base = {
        "id": snowflake(at),
        "channel": NS(id=channel),
        "author": NS(id=PROVIDER_A, bot=False),
        "content": content,
        "embeds": [],
        "reference": None,
        "mentions": [],
        "edited_at": None,
    }
    return NS(**{**base, **over})


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


@pytest.fixture
def feed() -> FeedSpy:
    return FeedSpy()


@pytest.fixture
def listener(factory: Factory, config: ConfigService, feed: FeedSpy) -> DiscordListener:
    return DiscordListener(
        None,
        sessions=factory,
        config=config,
        feed_health=feed,  # type: ignore[arg-type]
        alerts=AlertService(factory),
        clock=FixedClock(T0 + timedelta(minutes=1)),
    )


async def deliver(listener: DiscordListener, msg: NS) -> None:
    await listener.handle(mapping.create_payload(msg))


async def test_watched_channel_lands_in_inbox(
    listener: DiscordListener, feed: FeedSpy, db_session: AsyncSession
) -> None:
    await deliver(listener, message(T0, "Bought GOOGL 357.5C @ 1.67"))
    event = (await db_session.scalars(select(MessageEvent))).one()
    assert (event.channel_id, event.provider_id, event.text) == (
        CHANNEL,
        PROVIDER_A,
        "Bought GOOGL 357.5C @ 1.67",
    )
    assert await db_session.scalar(select(func.count()).select_from(InboxItem)) == 1
    assert len(feed.messages) == 1
    assert listener.status().last_message_at is not None


async def test_unwatched_channel_is_ignored(
    listener: DiscordListener, feed: FeedSpy, db_session: AsyncSession
) -> None:
    await deliver(listener, message(T0, "hello", channel=OTHER_CHANNEL))
    assert await db_session.scalar(select(func.count()).select_from(MessageEvent)) == 0
    assert feed.messages == []


async def test_duplicate_delivery_stores_once(
    listener: DiscordListener, db_session: AsyncSession
) -> None:
    msg = message(T0, "Bought GOOGL 357.5C @ 1.67")
    await deliver(listener, msg)
    await deliver(listener, msg)
    assert await db_session.scalar(select(func.count()).select_from(MessageEvent)) == 1


async def test_edit_and_delete_reference_the_original_author(
    listener: DiscordListener, db_session: AsyncSession
) -> None:
    msg = message(T0, "Bought GOOGL 357.5C @ 1.67")
    await deliver(listener, msg)
    edited = message(
        T0, "Bought GOOGL 357.5C @ 1.70", id=msg.id, edited_at=T0 + timedelta(seconds=5)
    )
    await listener.handle(mapping.edit_payload(edited))
    raw_delete = NS(message_id=msg.id, channel_id=CHANNEL, cached_message=None)
    await listener.handle_delete(raw_delete)  # type: ignore[arg-type]
    types = (
        await db_session.scalars(select(MessageEvent.event_type).order_by(MessageEvent.event_type))
    ).all()
    assert sorted(types) == sorted([EventType.CREATE, EventType.EDIT, EventType.DELETE])


async def test_delete_of_unknown_message_is_ignored(
    listener: DiscordListener, db_session: AsyncSession
) -> None:
    raw_delete = NS(message_id=snowflake(T0), channel_id=CHANNEL, cached_message=None)
    await listener.handle_delete(raw_delete)  # type: ignore[arg-type]
    assert await db_session.scalar(select(func.count()).select_from(MessageEvent)) == 0


async def test_thread_message_lands_in_the_parent_channel(
    listener: DiscordListener, db_session: AsyncSession
) -> None:
    """A thread id is never in the allowlist: the parent channel decides, the thread is kept."""
    thread = NS(id=OTHER_CHANNEL + 5, parent_id=CHANNEL)
    msg = message(T0, "SPY 765P .54")
    msg.channel = thread
    await deliver(listener, msg)
    event = (await db_session.scalars(select(MessageEvent))).one()
    assert (event.channel_id, event.thread_id) == (CHANNEL, OTHER_CHANNEL + 5)
    inbox_row = (await db_session.scalars(select(InboxItem))).one()
    assert inbox_row.channel_id == CHANNEL
    # a raw delete in the thread names the thread: the stored event resolves the parent
    await listener.handle_delete(
        NS(message_id=msg.id, channel_id=OTHER_CHANNEL + 5, cached_message=None)  # type: ignore[arg-type]
    )
    rows = (await db_session.scalars(select(MessageEvent).order_by(MessageEvent.event_type))).all()
    assert [(r.event_type, r.channel_id, r.thread_id) for r in rows] == [
        (EventType.CREATE, CHANNEL, OTHER_CHANNEL + 5),
        (EventType.DELETE, CHANNEL, OTHER_CHANNEL + 5),
    ]


async def test_thread_under_an_unwatched_channel_is_ignored(
    listener: DiscordListener, db_session: AsyncSession
) -> None:
    msg = message(T0, "SPY 765P .54")
    msg.channel = NS(id=OTHER_CHANNEL + 5, parent_id=OTHER_CHANNEL)
    await deliver(listener, msg)
    assert await db_session.scalar(select(func.count()).select_from(MessageEvent)) == 0


async def test_unconfigured_status(listener: DiscordListener) -> None:
    status = listener.status()
    assert status.state == "not_configured"
    assert [(c.id, c.visible) for c in status.channels] == [(CHANNEL, False)]


async def test_login_failure_raises_one_alert_and_stops(
    factory: Factory,
    config: ConfigService,
    feed: FeedSpy,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import discord
    from pydantic import SecretStr

    from app.alerts.models import Alert
    from app.discord_source import listener as module

    async def reject(self: Any, token: str, **_: Any) -> None:
        raise discord.LoginFailure("Improper token has been passed")

    monkeypatch.setattr(module._Client, "start", reject)
    monkeypatch.setattr(module._Client, "close", lambda self: _done())
    bad = DiscordListener(
        SecretStr("bad"),
        sessions=factory,
        config=config,
        feed_health=feed,  # type: ignore[arg-type]
        alerts=AlertService(factory),
        clock=FixedClock(T0),
    )
    await bad.run()
    await bad.run()  # a restart does not add a second alert
    assert bad.status().state == "login_failed"
    alerts = (await db_session.scalars(select(Alert))).all()
    assert [(a.kind, a.severity) for a in alerts] == [("message_api", "urgent")]


async def _done() -> None:
    return None
