"""F039 / PRD 7.7: off-hours messages update the ledger now and get their cards at 9:30 ET,
built with fresh validation; validity starts at the release (OD-29); no order off-hours."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.cards.models import Card, CardAction, CardState
from app.inbox.envelope import Envelope
from app.inbox.queries import list_feed
from app.ledger.models import ProviderPosition
from app.pipeline.release import ReleaseService
from app.system.flags import FlagService
from app.timers.models import Timer, TimerKind, TimerStatus
from app.timers.service import TimerService
from tests.integration.graph.conftest import World
from tests.integration.pipeline.harness import follow_up, opened_position, option_entry
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph
EVENING = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)  # Monday 20:00 ET
TUESDAY_OPEN = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)  # 9:30 ET


def at(w: World, when: datetime) -> None:
    w.h.clock.advance(when - w.h.clock.now())


async def entry_at(w: World, when: datetime) -> Envelope:
    at(w, when)
    env = await w.h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        at=when,
        author=PROVIDER_A,
    )
    await w.h.process(env, processor=w.processor)
    return env


async def cards(w: World) -> list[Card]:
    async with w.h.factory() as db:
        return list((await db.scalars(select(Card))).all())


async def release_timers(w: World) -> list[Timer]:
    async with w.h.factory() as db:
        rows = await db.scalars(select(Timer).where(col(Timer.kind) == TimerKind.OFFHOURS_RELEASE))
        return list(rows.all())


def timer_service(w: World) -> TimerService:
    timers = TimerService(w.h.factory, alerts=w.h.alerts, clock=w.h.clock)
    release = ReleaseService(
        w.h.factory,
        planner=w.h.planner(FlagService(w.h.factory, w.h.alerts)),
        config=w.h.config,
        clock=w.h.clock,
        approvals=w.approvals,
    )
    timers.register(TimerKind.OFFHOURS_RELEASE, release.on_release)
    return timers


async def test_evening_entry_is_held_then_released_at_the_open(w: World) -> None:
    env = await entry_at(w, EVENING)
    assert await cards(w) == []
    async with w.h.factory() as db:
        assert await db.get(ProviderPosition, opened_position(env)) is not None  # ledger now
        feed, _ = await list_feed(db, provider_id=None, label=None, cursor=None, limit=10)
    assert "held_until_open" in feed[0].labels
    [timer] = await release_timers(w)
    assert timer.due_at == TUESDAY_OPEN and timer.message_id == env.key.message_id

    timers = timer_service(w)
    at(w, TUESDAY_OPEN - timedelta(seconds=1))
    assert await timers.run_due() == 0
    at(w, TUESDAY_OPEN)
    assert await timers.run_due() == 1
    [card] = await cards(w)
    assert card.state == CardState.PENDING and card.action == CardAction.ENTRY
    assert card.provider_position_id == opened_position(env)
    assert card.valid_until == TUESDAY_OPEN + timedelta(seconds=60)  # OD-29: from the release
    assert await w.thread_next(card.card_id) == ("await_approval",)
    await w.decide(card.card_id)
    assert (await w.card(card.card_id)).state == CardState.SUBMITTED
    async with w.h.factory() as db:
        [timer] = (
            await db.scalars(select(Timer).where(col(Timer.kind) == TimerKind.OFFHOURS_RELEASE))
        ).all()
        assert timer.status == TimerStatus.FIRED


async def test_position_closed_overnight_gets_no_entry_card(w: World) -> None:
    env = await entry_at(w, EVENING)
    at(w, EVENING + timedelta(hours=1))
    out = await w.h.message(
        "all out",
        [follow_up("full_exit", opened_position(env))],
        at=EVENING + timedelta(hours=1),
        author=PROVIDER_A,
    )
    await w.h.process(out, processor=w.processor)
    at(w, TUESDAY_OPEN)
    await timer_service(w).run_due()
    assert await cards(w) == []
    async with w.h.factory() as db:
        position = await db.get(ProviderPosition, opened_position(env))
    assert position is not None and position.status == "closed"


async def test_friday_evening_waits_for_monday(w: World) -> None:
    friday = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)  # Friday 2026-10-02 20:00 ET
    await entry_at(w, friday)
    [timer] = await release_timers(w)
    assert timer.due_at == datetime(2026, 10, 5, 13, 30, tzinfo=UTC)


async def test_no_order_after_the_close(w: World) -> None:
    close = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)  # Monday 16:00 ET
    await entry_at(w, close - timedelta(seconds=10))
    [card] = await cards(w)
    at(w, close + timedelta(seconds=5))  # still within its 60 s validity
    await w.decide(card.card_id)
    assert (await w.card(card.card_id)).state == CardState.INVALIDATED
    assert await w.orders() == []
