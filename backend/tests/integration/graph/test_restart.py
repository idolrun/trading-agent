"""F046 / AC-7: a crash between the committed order intent and the broker ack, then restart.

The startup sequence adopts the order by client id, resumes the approved card without a second
submit, expires the card that was still pending, and re-opens entries once broker and lots agree.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.broker.fake import FakeBroker
from app.broker.port import BrokerOrder, OrderRequest
from app.cards.models import Card, CardState
from app.execution.consumer import BrokerEventConsumer
from app.execution.expiration import ExpirationService
from app.graph.checkpointer import open_pool, saver_and_store
from app.ingest.gaps import GapDetector
from app.main import libpq_dsn
from app.messageapi.fake import FakeMessageHistory
from app.reconcile.service import Reconciler
from app.reconcile.startup import startup
from app.runtime_config.schema import TradingMode
from app.system.flags import FlagService
from app.system.models import FlagName
from app.timers.service import TimerService
from tests.integration.conftest import _url
from tests.integration.graph.conftest import World
from tests.integration.pipeline.harness import T0, Factory, Harness, option_entry
from tests.integration.schema.helpers import add_session
from tests.messages import PROVIDER_A, PROVIDER_B

pytestmark = pytest.mark.graph


TEXT = "Bought GOOGL 357.5C 10/2 @ 0.71"
ENTRY = option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")


class Killed(BaseException):
    """The process dies: not an Exception, so nothing in the app handles it."""


class CrashingBroker(FakeBroker):
    """Accepts the order, then the process is killed before the ack is stored."""

    crash = True

    async def submit(self, req: OrderRequest) -> BrokerOrder:
        order = await super().submit(req)
        if self.crash:
            raise Killed
        return order


class DispatcherStub:
    started = 0

    async def start(self) -> None:
        self.started += 1


@pytest.fixture
async def paper(factory: Factory, tmp_path: Path, db_session: object) -> AsyncIterator[World]:
    pool = open_pool(libpq_dsn(_url("test_database_url")))
    await pool.open()
    try:
        saver, _ = saver_and_store(pool)
        h = await Harness.create(factory, tmp_path, trading_mode=TradingMode.PAPER)
        broker = CrashingBroker(h.clock)
        approvals = h.approval_service(saver, broker)
        yield World(
            h,
            broker,
            approvals,
            h.processor(approvals=approvals),
            saver,
            await add_session(db_session),  # type: ignore[arg-type]
        )
    finally:
        await pool.close()


async def card_for(w: World, author: int, at: datetime) -> uuid.UUID:
    env = await w.h.message(TEXT, [ENTRY], at=at, author=author)
    await w.h.process(env, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.provider_id) == author))
    assert card is not None and card.state == CardState.PENDING
    return card.card_id


async def test_ac7_crash_before_ack_then_startup_adopts_without_duplicate(paper: World) -> None:
    w, broker = paper, paper.broker
    assert isinstance(broker, CrashingBroker)
    approved = await card_for(w, PROVIDER_A, T0)
    pending = await card_for(w, PROVIDER_B, T0 + timedelta(seconds=1))
    with pytest.raises(Killed):
        await w.decide(approved)
    [order] = await w.orders()
    assert order.status == "intent" and order.broker_order_id is None  # committed, never acked
    assert (await w.card(approved)).state == CardState.APPROVED
    assert await w.thread_next(approved) == ("execute",)
    assert [c for c, _ in broker.calls] == ["submit"]

    # restart: a new process (fresh services, same database and checkpointer)
    broker.crash = False
    factory, h = w.h.factory, w.h
    w.approvals = approvals = h.approval_service(w.saver, broker)
    exits = approvals._executor.exit
    assert exits is not None
    flags = FlagService(factory, h.alerts)
    dispatcher = DispatcherStub()
    report = await startup(
        sessions=factory,
        flags=flags,
        reconciler=Reconciler(
            factory,
            broker=broker,
            consumer=BrokerEventConsumer(factory, [broker]),
            flags=flags,
            alerts=h.alerts,
            config=h.config,
            clock=h.clock,
        ),
        approvals=approvals,
        gaps=GapDetector(
            factory,
            history=FakeMessageHistory(),
            config=h.config,
            alerts=h.alerts,
            clock=h.clock,
        ),
        timers=TimerService(factory, alerts=h.alerts, clock=h.clock),
        expiration=ExpirationService(
            factory,
            config=h.config,
            clock=h.clock,
            approvals=approvals,
            exits=exits,
            alerts=h.alerts,
        ),
        exits=exits,
        dispatcher=dispatcher,  # type: ignore[arg-type]
    )

    assert report.clean and report.orders_synced == 1
    [adopted] = await w.orders()
    assert adopted.order_id == order.order_id
    assert adopted.status == "working" and adopted.broker_order_id is not None
    assert [c for c, _ in broker.calls if c == "submit"] == ["submit"]  # no duplicate submit
    assert (await w.card(approved)).state == CardState.SUBMITTED
    assert await w.thread_next(approved) == ()
    assert (await w.card(pending)).state == CardState.EXPIRED
    assert await w.thread_next(pending) == ()
    assert dispatcher.started == 1
    assert not await flags.is_active(FlagName.ENTRIES_BLOCKED)  # broker and lots agree


async def test_approved_card_whose_resume_never_ran_is_executed_after_restart(paper: World) -> None:
    w = paper
    assert isinstance(w.broker, CrashingBroker)
    w.broker.crash = False
    card_id = await card_for(w, PROVIDER_A, T0)

    async def lost(*_args: object) -> None:  # the process died right after the approval commit
        return None

    real, w.approvals.resume = w.approvals.resume, lost  # type: ignore[method-assign]
    await w.decide(card_id)
    w.approvals.resume = real  # type: ignore[method-assign]
    assert (await w.card(card_id)).state == CardState.APPROVED and await w.orders() == []
    assert await w.thread_next(card_id) == ("await_approval",)

    assert await w.approvals.resume_approved() == 1
    assert (await w.card(card_id)).state == CardState.SUBMITTED
    assert len(await w.orders()) == 1
    assert await w.approvals.resume_approved() == 0  # nothing left to continue
