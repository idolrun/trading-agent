"""F036: the per-card approval graph — every resume value, revalidation, AC-2, AC-4, AC-7."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.cards.approval_service import DecisionConflict
from app.cards.models import Approval, CardState
from app.execution.card_executor import CardExecutor
from app.execution.consumer import BrokerEventConsumer
from app.instruments import OptionContract
from app.ledger.models import Lot
from app.timers.models import Timer, TimerKind
from app.timers.service import TimerService
from tests.integration.graph.conftest import World
from tests.integration.pipeline.harness import (
    T0,
    follow_up,
    lit,
    opened_position,
)
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)


async def test_new_pending_card_waits_in_interrupt(w: World) -> None:
    _, card_id = await w.entry()
    assert await w.thread_next(card_id) == ("await_approval",)


async def test_approve_revalidates_and_submits_one_approved_order(w: World) -> None:
    _, card_id = await w.entry()
    await w.decide(card_id)
    card = await w.card(card_id)
    assert card.state == CardState.SUBMITTED
    [order] = await w.orders()
    async with w.h.factory() as db:
        approval = await db.scalar(select(Approval).where(col(Approval.card_id) == card_id))
    assert approval is not None and order.approval_id == approval.approval_id  # AC-2
    assert order.card_id == card_id and order.qty == 13 and order.limit_price == Decimal("0.74")
    assert await w.thread_next(card_id) == ()


async def test_reject_ends_the_thread_without_order(w: World) -> None:
    _, card_id = await w.entry()
    await w.decide(card_id, "reject")
    assert (await w.card(card_id)).state == CardState.REJECTED
    assert await w.orders() == [] and await w.thread_next(card_id) == ()


async def test_expiry_timer_expires_and_resumes(w: World) -> None:
    _, card_id = await w.entry()
    timers = TimerService(w.h.factory, alerts=w.h.alerts, clock=w.h.clock)
    timers.register(TimerKind.CARD_EXPIRY, w.approvals.on_card_expiry)
    w.h.clock.advance(timedelta(minutes=2))
    assert await timers.run_due() == 1
    assert (await w.card(card_id)).state == CardState.EXPIRED
    assert await w.thread_next(card_id) == ()
    await w.approvals.on_card_expiry(await _timer(w, card_id))  # a late duplicate: no-op
    assert await w.orders() == []


async def test_restart_expires_open_cards(w: World) -> None:
    _, card_id = await w.entry()
    assert await w.approvals.expire_on_restart() == 1
    card = await w.card(card_id)
    assert card.state == CardState.EXPIRED and await w.thread_next(card_id) == ()


async def test_ac4_timeout_at_revalidation_places_nothing(w: World) -> None:
    _, card_id = await w.entry()
    w.h.clock.advance(timedelta(minutes=2))  # past valid_until, timer not yet run
    await w.decide(card_id)
    assert (await w.card(card_id)).state == CardState.INVALIDATED
    assert await w.orders() == []


async def test_ac4_ask_above_band_at_revalidation_places_nothing(w: World) -> None:
    _, card_id = await w.entry()
    w.h.md.set_quote(GOOGL, Decimal("0.78"), Decimal("0.80"))  # > 0.71 + 5%
    await w.decide(card_id)
    assert (await w.card(card_id)).state == CardState.INVALIDATED
    assert await w.orders() == []


async def test_ac4_superseding_stop_invalidates_and_resumes(w: World) -> None:
    env, card_id = await w.entry()
    stop = await w.h.message(
        "stop 0.50",
        [follow_up("stop_set", opened_position(env), stop_level=lit(Decimal("0.50"), "0.50"))],
        at=T0 + timedelta(seconds=3),
        author=PROVIDER_A,
    )
    await w.h.process(stop, processor=w.processor)
    assert (await w.card(card_id)).state == CardState.INVALIDATED
    assert await w.thread_next(card_id) == ()
    with pytest.raises(DecisionConflict):
        await w.decide(card_id)
    assert await w.orders() == []


async def test_ac7_crash_after_intent_commit_resumes_without_duplicate(w: World) -> None:
    _, card_id = await w.entry()
    real = w.approvals._executor

    class Crashing(CardExecutor):
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            await real.execute(*args, **kwargs)
            raise RuntimeError("process killed after the order was placed")

    w.approvals._executor = Crashing(w.h.factory, orders=real._orders, marketdata=w.h.md)
    await w.decide(card_id)  # the failed run is logged; the approval stays committed
    assert (await w.card(card_id)).state == CardState.APPROVED
    assert await w.thread_next(card_id) == ("execute",)
    w.approvals._executor = real
    await w.approvals.resume(card_id, {"decision": "expire", "reason": "restart"})
    assert (await w.card(card_id)).state == CardState.SUBMITTED
    assert len(await w.orders()) == 1
    assert [c for c, _ in w.broker.calls if c == "submit"] == ["submit"]


async def _timer(w: World, card_id: uuid.UUID) -> Timer:
    async with w.h.factory() as db:
        timer = await db.scalar(select(Timer).where(col(Timer.card_id) == card_id))
    assert timer is not None
    return timer


async def test_m4_message_to_lot_in_shadow_mode(w: World) -> None:
    """Message -> card -> approval -> shadow order filled at the ask -> lot (F040)."""
    env, card_id = await w.entry()
    await w.decide(card_id)
    consumer = BrokerEventConsumer(w.h.factory, [w.broker])
    for event in w.broker.pending_events():
        await consumer.handle(event)
    [order] = await w.orders()
    assert order.status == "filled" and order.lot_id is not None
    async with w.h.factory() as db:
        lot = await db.get(Lot, order.lot_id)
    assert lot is not None and lot.qty == 13 and lot.avg_price == Decimal("0.71")
    assert lot.provider_position_id == opened_position(env)
