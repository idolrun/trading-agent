"""F036: the per-card approval graph — every resume value, revalidation, AC-2, AC-4, AC-7."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.broker.simulated import SimulatedBroker
from app.cards.approval_service import ApprovalService, DecisionConflict
from app.cards.edits import Edits
from app.cards.models import Approval, Card, CardAction, CardState
from app.db import create_session_factory
from app.execution.card_executor import CardExecutor
from app.execution.models import Order
from app.graph.approval_graph import thread_config
from app.graph.checkpointer import open_pool, saver_and_store
from app.graph.processor import GraphProcessor
from app.inbox.envelope import Envelope
from app.instruments import OptionContract
from app.main import libpq_dsn
from app.timers.models import Timer, TimerKind
from app.timers.service import TimerService
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import (
    T0,
    Factory,
    Harness,
    follow_up,
    lit,
    opened_position,
    option_entry,
)
from tests.integration.schema.helpers import add_session
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)


async def _never() -> bool:
    return False


@dataclass
class World:
    h: Harness
    broker: SimulatedBroker
    approvals: ApprovalService
    processor: GraphProcessor
    saver: AsyncPostgresSaver
    session_id: uuid.UUID

    async def entry(self) -> tuple[Envelope, uuid.UUID]:
        env = await self.h.message(
            "Bought GOOGL 357.5C 10/2 @ 0.71",
            [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
            author=PROVIDER_A,
        )
        await self.h.process(env, processor=self.processor)
        async with self.h.factory() as db:
            card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
        assert card is not None and card.state == CardState.PENDING
        return env, card.card_id

    async def decide(self, card_id: uuid.UUID, decision: Any = "approve", **edits: Any) -> None:
        await self.approvals.decide(
            card_id,
            decision,
            1,
            Edits(**edits),
            session_id=self.session_id,
            consume_step_up=_never,
        )

    async def card(self, card_id: uuid.UUID) -> Card:
        async with self.h.factory() as db:
            card = await db.get(Card, card_id)
        assert card is not None
        return card

    async def orders(self) -> list[Order]:
        async with self.h.factory() as db:
            return list((await db.scalars(select(Order))).all())

    async def thread_next(self, card_id: uuid.UUID) -> tuple[str, ...]:
        graph = self.approvals._graph
        snap = await graph.aget_state(thread_config(card_id))
        assert snap.created_at is not None, "approval thread was never started"
        return tuple(snap.next)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def w(factory: Factory, tmp_path: Path, db_session: AsyncSession) -> AsyncIterator[World]:
    pool = open_pool(libpq_dsn(_url("test_database_url")))
    await pool.open()
    try:
        saver, _ = saver_and_store(pool)
        h = await Harness.create(factory, tmp_path)
        broker = SimulatedBroker(h.md, h.clock)
        approvals = h.approval_service(saver, broker)
        yield World(
            h,
            broker,
            approvals,
            h.processor(approvals=approvals),
            saver,
            await add_session(db_session),
        )
    finally:
        await pool.close()


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
