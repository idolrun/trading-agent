"""F039 / PRD 7.7: off-hours messages update the ledger now and get their cards at 9:30 ET,
built with fresh validation; validity starts at the release (OD-29); no order off-hours."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.cards.models import Card, CardAction, CardState
from app.inbox.envelope import Envelope
from app.inbox.queries import list_feed
from app.interpret.models import Signal
from app.ledger.models import ProviderPosition
from app.pipeline.release import ReleaseService
from app.system.flags import FlagService
from app.timers.models import Timer, TimerKind, TimerStatus
from app.timers.service import TimerService
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.pipeline.harness import follow_up, opened_position, option_entry
from tests.messages import PROVIDER_A, PROVIDER_B

pytestmark = pytest.mark.graph
EVENING = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)  # Monday 20:00 ET
TUESDAY_OPEN = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)  # 9:30 ET


def at(w: World, when: datetime) -> None:
    w.h.clock.advance(when - w.h.clock.now())


async def entry_at(
    w: World, when: datetime, price: str = "0.71", author: int = PROVIDER_A
) -> Envelope:
    at(w, when)
    env = await w.h.message(
        f"Bought GOOGL 357.5C 10/2 @ {price}",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", price)],
        at=when,
        author=author,
    )
    await w.h.process(env, processor=w.processor)
    return env


async def edit_at(w: World, original: Envelope, when: datetime, price: str) -> Envelope:
    at(w, when)
    env = await w.h.edit(
        original,
        f"Bought GOOGL 357.5C 10/2 @ {price}",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", price)],
        at=when,
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


def quote_near_one_seventy(w: World) -> None:
    """The fixture quote is 0.70/0.71: move it so 1.67-1.80 pass the identity band (FR-11)."""
    w.h.md.set_quote(GOOGL, Decimal("1.70"), Decimal("1.75"))


async def released_cards(w: World) -> list[Card]:
    at(w, TUESDAY_OPEN)
    await timer_service(w).run_due()
    return await cards(w)


async def test_edit_while_held_releases_the_edited_entry(w: World) -> None:
    quote_near_one_seventy(w)
    """create @ 1.67 held, edited to 1.75 before the open: the card is for 1.75 (PRD 7.5, 7.7)."""
    env = await entry_at(w, EVENING, "1.67")
    await edit_at(w, env, EVENING + timedelta(minutes=5), "1.75")
    assert await cards(w) == []
    [card] = await released_cards(w)
    print("PROPOSAL", card.proposal)
    assert card.state == CardState.PENDING and card.action == CardAction.ENTRY
    assert card.proposal["signal_price"] == "1.75"
    assert card.provider_position_id == opened_position(env)
    async with w.h.factory() as db:
        position = await db.get(ProviderPosition, opened_position(env))
    assert position is not None and position.provider_entry_price == Decimal("1.75")
    assert await w.orders() == []


async def test_several_edits_while_held_release_the_latest(w: World) -> None:
    quote_near_one_seventy(w)
    env = await entry_at(w, EVENING, "1.67")
    await edit_at(w, env, EVENING + timedelta(minutes=5), "1.75")
    await edit_at(w, env, EVENING + timedelta(minutes=10), "1.80")
    [card] = await released_cards(w)  # no stale 1.67 / 1.75 card
    assert card.proposal["signal_price"] == "1.80"


async def test_edit_after_the_open_before_the_release_timer_runs(w: World) -> None:
    quote_near_one_seventy(w)
    env = await entry_at(w, EVENING, "1.67")
    await edit_at(w, env, TUESDAY_OPEN, "1.75")
    [card] = await released_cards(w)
    assert card.proposal["signal_price"] == "1.75"


async def test_edit_after_release_replaces_the_card(w: World) -> None:
    quote_near_one_seventy(w)
    env = await entry_at(w, EVENING, "1.67")
    [first] = await released_cards(w)
    assert first.proposal["signal_price"] == "1.67"
    await edit_at(w, env, TUESDAY_OPEN + timedelta(seconds=5), "1.75")
    by_state = {c.state: c for c in await cards(w)}
    assert by_state[CardState.INVALIDATED].card_id == first.card_id
    assert by_state[CardState.PENDING].proposal["signal_price"] == "1.75"
    assert len(by_state) == 2 and await w.orders() == []


async def test_edit_after_the_trade_alerts_and_proposes_nothing(w: World) -> None:
    quote_near_one_seventy(w)
    env = await entry_at(w, EVENING, "1.67")
    [card] = await released_cards(w)
    await w.decide(card.card_id)
    [order] = await w.orders()
    await edit_at(w, env, TUESDAY_OPEN + timedelta(seconds=5), "1.75")
    async with w.h.factory() as db:
        kind = col(Alert.kind) == AlertKind.LATE_CORRECTION
        alerts = (await db.scalars(select(Alert).where(kind))).all()
    assert len(alerts) == 1
    assert [c.card_id for c in await cards(w)] == [card.card_id]
    assert [o.order_id for o in await w.orders()] == [order.order_id]


async def test_duplicate_delivery_and_double_release_stay_idempotent(w: World) -> None:
    quote_near_one_seventy(w)
    env = await entry_at(w, EVENING, "1.67")
    edit = await edit_at(w, env, EVENING + timedelta(minutes=5), "1.75")
    await w.h.process(edit, processor=w.processor)  # redelivered edit
    await w.h.process(env, processor=w.processor)  # redelivered create
    [card] = await released_cards(w)
    await timer_service(w).run_due()
    assert [c.card_id for c in await cards(w)] == [card.card_id]
    assert card.proposal["signal_price"] == "1.75"
    async with w.h.factory() as db:
        signals = (await db.scalars(select(Signal).where(col(Signal.event_type) == "create"))).all()
    assert len(signals) == 1 and await w.orders() == []


async def test_providers_stay_isolated(w: World) -> None:
    quote_near_one_seventy(w)
    a = await entry_at(w, EVENING, "1.67", PROVIDER_A)
    b = await entry_at(w, EVENING + timedelta(minutes=1), "1.78", PROVIDER_B)
    await edit_at(w, a, EVENING + timedelta(minutes=5), "1.75")
    got = {c.provider_id: c.proposal["signal_price"] for c in await released_cards(w)}
    assert got == {PROVIDER_A: "1.75", PROVIDER_B: "1.78"}
    assert b.provider_id == PROVIDER_B
