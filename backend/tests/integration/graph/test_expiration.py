"""F045 / AC-6: a 0DTE lot gets an urgent close card at cutoff 1 and, unanswered, a forced
close at cutoff 2 after its protective orders are cancelled; half days shift the cutoffs."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.cards.models import Card, CardAction, CardSource, CardState
from app.execution.expiration import ExpirationService, cutoffs
from app.execution.models import AutoReason, Order, OrderKind, OrderStatus
from app.ledger.models import Lot, LotStatus
from app.timers.models import TimerKind
from app.timers.service import TimerService
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.graph.test_brackets import TEXT, bracketed_entry, working
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph
FRIDAY = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)  # 10:30 ET, GOOGL 10/2 expires today
CUTOFF_1 = datetime(2026, 10, 2, 19, 30, tzinfo=UTC)  # 16:00 ET close - 30 min
CUTOFF_2 = datetime(2026, 10, 2, 19, 50, tzinfo=UTC)  # - 10 min


def at(w: World, when: datetime) -> None:
    w.h.clock.advance(when - w.h.clock.now())


async def zero_dte(w: World) -> tuple[ExpirationService, TimerService]:
    at(w, FRIDAY)
    env = await w.h.message(TEXT, [bracketed_entry()], at=FRIDAY, author=PROVIDER_A)
    await w.h.process(env, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
    assert card is not None
    await w.decide(card.card_id, qty=2)
    await w.consume()
    exits = w.approvals._executor.exit
    assert exits is not None
    expiration = ExpirationService(
        w.h.factory,
        config=w.h.config,
        clock=w.h.clock,
        approvals=w.approvals,
        exits=exits,
        alerts=w.h.alerts,
    )
    assert await expiration.schedule_today() == 1
    timers = TimerService(w.h.factory, alerts=w.h.alerts, clock=w.h.clock)
    timers.register(TimerKind.CUTOFF_1, expiration.on_cutoff_1)
    timers.register(TimerKind.CUTOFF_2, expiration.on_cutoff_2)
    return expiration, timers


async def close_card(w: World) -> Card:
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.CLOSE))
    assert card is not None
    return card


async def exits(w: World) -> list[Order]:
    async with w.h.factory() as db:
        rows = await db.scalars(
            select(Order).where(col(Order.kind) == OrderKind.EXIT).order_by(col(Order.created_at))
        )
        return list(rows.all())


async def test_ac6_unanswered_close_card_then_forced_close(w: World) -> None:
    _, timers = await zero_dte(w)
    assert (await working(w))[OrderKind.STOP]  # the bracket is on
    at(w, CUTOFF_1)
    assert await timers.run_due() == 1
    card = await close_card(w)
    assert card.source == CardSource.SYSTEM and card.state == CardState.PENDING
    assert card.proposal["qty"] == 2 and card.proposal["labels"] == ["urgent"]
    assert await w.thread_next(card.card_id) == ("await_approval",)

    at(w, CUTOFF_2)
    assert await timers.run_due() == 1
    await w.exits_done()
    assert (await close_card(w)).state == CardState.INVALIDATED
    assert await w.thread_next(card.card_id) == ()
    root, *_ = await exits(w)
    assert root.auto_reason == AutoReason.EOD_FORCED_CLOSE and root.approval_id is None
    async with w.h.factory() as db:
        lot = await db.scalar(select(Lot))
        stops = (
            await db.scalars(
                select(Order).where(col(Order.kind).in_([OrderKind.STOP, OrderKind.TP]))
            )
        ).all()
        [alert] = (
            await db.scalars(select(Alert).where(col(Alert.kind) == AlertKind.EOD_FORCED_CLOSE))
        ).all()
    assert lot is not None and lot.status == LotStatus.CLOSED
    assert all(o.status == OrderStatus.CANCELLED for o in stops)  # cancelled before the exit
    assert all(o.created_at <= root.created_at for o in stops)
    assert alert.details["qty"] == 2


async def test_approved_close_before_cutoff_2_means_no_forced_close(w: World) -> None:
    _, timers = await zero_dte(w)
    at(w, CUTOFF_1)
    await timers.run_due()
    w.h.md.set_quote(GOOGL, Decimal("0.71"), Decimal("0.71"))  # the close fills at once
    await w.decide((await close_card(w)).card_id)
    await w.exits_done()
    at(w, CUTOFF_2)
    await timers.run_due()
    [root] = [o for o in await exits(w) if o.parent_order_id is None]
    assert root.approval_id is not None and root.auto_reason is None
    async with w.h.factory() as db:
        assert (
            await db.scalars(select(Alert).where(col(Alert.kind) == AlertKind.EOD_FORCED_CLOSE))
        ).all() == []


def test_half_day_shifts_the_cutoffs() -> None:
    # day after Thanksgiving 2026: 13:00 ET close (18:00 UTC)
    assert cutoffs(date(2026, 11, 27), 30, 10) == (
        datetime(2026, 11, 27, 17, 30, tzinfo=UTC),
        datetime(2026, 11, 27, 17, 50, tzinfo=UTC),
    )
    assert cutoffs(date(2026, 11, 28), 30, 10) is None  # Saturday
