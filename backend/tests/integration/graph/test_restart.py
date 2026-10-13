"""F046 / AC-7: a crash between the committed order intent and the broker ack, then restart.

The startup sequence adopts the order by client id, resumes the approved card without a second
submit, expires the card that was still pending, and re-opens entries once broker and lots agree.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.broker.errors import BrokerUnavailable
from app.broker.fake import FakeBroker
from app.broker.port import BrokerOrder, BrokerPosition, OrderRequest
from app.cards.models import Card, CardState
from app.db import in_transaction
from app.execution.consumer import BrokerEventConsumer
from app.execution.expiration import ExpirationService
from app.graph.approval_graph import thread_config
from app.graph.checkpointer import open_pool, saver_and_store
from app.inbox import repo as inbox
from app.inbox.models import InboxItem, InboxStatus
from app.main import libpq_dsn
from app.reconcile.service import Reconciler, ReconcileReport
from app.reconcile.startup import recover_after_failover, startup
from app.runtime_config.models import Actor
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


def recovery_services(w: World, broker: FakeBroker, flags: FlagService) -> dict[str, Any]:
    """What `startup()` and `recover_after_failover()` share, over `w.approvals`."""
    factory, h = w.h.factory, w.h
    return {
        "flags": flags,
        "reconciler": Reconciler(
            factory,
            broker=broker,
            consumer=BrokerEventConsumer(factory, [broker]),
            flags=flags,
            alerts=h.alerts,
            config=h.config,
            clock=h.clock,
        ),
        "approvals": w.approvals,
        "timers": TimerService(factory, alerts=h.alerts, clock=h.clock),
    }


async def restart(
    w: World, broker: FakeBroker
) -> tuple[ReconcileReport, FlagService, DispatcherStub]:
    """A new process: fresh services on the same database and checkpointer, then `startup()`."""
    factory, h = w.h.factory, w.h
    w.approvals = approvals = h.approval_service(w.saver, broker)
    approvals.unblock_poll_s = 0.05
    exits = approvals._executor.exit
    assert exits is not None
    flags = FlagService(factory, h.alerts)
    dispatcher = DispatcherStub()
    report = await startup(
        sessions=factory,
        **recovery_services(w, broker, flags),
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
    return report, flags, dispatcher


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
    report, flags, dispatcher = await restart(w, broker)

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


async def test_approved_entry_resumed_by_startup_is_executed_not_blocked(paper: World) -> None:
    """The approval is resumed after the entries gate: revalidating under the startup block
    would invalidate it ("entries blocked") although reconciliation is clean."""
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

    report, flags, _ = await restart(w, w.broker)

    assert report.clean and not await flags.is_active(FlagName.ENTRIES_BLOCKED)
    assert (await w.card(card_id)).state == CardState.SUBMITTED
    assert len(await w.orders()) == 1


async def approved_not_resumed(w: World) -> uuid.UUID:
    """An approved entry whose resume never ran (the process died after the approval commit)."""
    card_id = await card_for(w, PROVIDER_A, T0)

    async def lost(*_args: object) -> None:
        return None

    real, w.approvals.resume = w.approvals.resume, lost  # type: ignore[method-assign]
    await w.decide(card_id)
    w.approvals.resume = real  # type: ignore[method-assign]
    assert (await w.card(card_id)).state == CardState.APPROVED and await w.orders() == []
    return card_id


def broker_unreadable(broker: FakeBroker) -> Callable[[], None]:
    """`positions()` fails: reconciliation cannot verify the book (dirty). Returns the fix."""
    real = broker.positions

    async def down() -> list[BrokerPosition]:
        raise BrokerUnavailable("broker unreadable")

    broker.positions = down  # type: ignore[method-assign]
    return lambda: setattr(broker, "positions", real)


async def eventually_state(w: World, card_id: uuid.UUID, state: CardState) -> None:
    async with asyncio.timeout(5):
        while (await w.card(card_id)).state != state:  # noqa: ASYNC110 — a background resume
            await asyncio.sleep(0.05)


async def test_dirty_reconcile_on_startup_keeps_the_approved_entry_until_unblocked(
    paper: World,
) -> None:
    w = paper
    assert isinstance(w.broker, CrashingBroker)
    w.broker.crash = False
    card_id = await approved_not_resumed(w)
    fix = broker_unreadable(w.broker)

    report, flags, _ = await restart(w, w.broker)
    try:
        assert not report.clean and await flags.is_active(FlagName.ENTRIES_BLOCKED)
        await asyncio.sleep(0.2)  # the deferred resume polls while the block stays up
        assert (await w.card(card_id)).state == CardState.APPROVED  # not invalidated
        assert await w.orders() == []
        assert await w.thread_next(card_id) == ("await_approval",)

        fix()
        await flags.set(FlagName.ENTRIES_BLOCKED, False, reason="manual fix", actor=Actor.USER)
        await eventually_state(w, card_id, CardState.SUBMITTED)
        assert len(await w.orders()) == 1
    finally:
        await w.approvals.stop()


async def test_dirty_reconcile_after_failover_keeps_the_approved_entry(paper: World) -> None:
    w = paper
    assert isinstance(w.broker, CrashingBroker)
    w.broker.crash = False
    w.approvals.unblock_poll_s = 0.05
    card_id = await approved_not_resumed(w)
    fix = broker_unreadable(w.broker)
    flags = FlagService(w.h.factory, w.h.alerts)

    report = await recover_after_failover(**recovery_services(w, w.broker, flags))
    try:
        assert not report.clean and await flags.is_active(FlagName.ENTRIES_BLOCKED)
        await asyncio.sleep(0.2)
        assert (await w.card(card_id)).state == CardState.APPROVED
        assert await w.orders() == []

        fix()  # the next clean reconciliation lifts the failover block
        assert (await recovery_services(w, w.broker, flags)["reconciler"].run()).clean
        await eventually_state(w, card_id, CardState.SUBMITTED)
        assert len(await w.orders()) == 1
    finally:
        await w.approvals.stop()


async def test_failure_after_apply_keeps_done_and_a_rerun_starts_the_approval_graph(
    paper: World,
) -> None:
    """`notify` fails after the apply committed: the event stays `done` (a retry mark is a
    no-op), and a re-run of the event reloads its card ids and starts the approval graph."""
    w = paper
    env = await w.h.message(TEXT, [ENTRY], at=T0, author=PROVIDER_A)

    async def notify_fails(_ids: object) -> None:
        raise RuntimeError("notify failed")

    real, w.approvals.start_many = w.approvals.start_many, notify_fails  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="notify failed"):
        await w.h.process(env, processor=w.processor)
    w.approvals.start_many = real  # type: ignore[method-assign]

    async with in_transaction(w.h.factory, None) as db:
        assert not await inbox.mark(db, env.key, InboxStatus.RECEIVED, error="notify failed")
        assert not await inbox.mark(db, env.key, InboxStatus.FAILED, error="notify failed")
    async with w.h.factory() as db:
        row = await db.get(InboxItem, tuple(env.key))
        card = await db.scalar(select(Card).where(col(Card.provider_id) == PROVIDER_A))
    assert row is not None and row.status == InboxStatus.DONE and row.last_error is None
    assert card is not None and card.state == CardState.PENDING
    snap = await w.approvals._graph.aget_state(thread_config(card.card_id))
    assert snap.created_at is None  # the approval graph never started

    await w.h.process(env, attempt=2, processor=w.processor)  # idempotent re-apply

    assert await w.thread_next(card.card_id) == ("await_approval",)
    async with w.h.factory() as db:
        assert len((await db.scalars(select(Card))).all()) == 1  # nothing written twice
