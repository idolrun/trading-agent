"""F046: reconciliation against a FakeBroker (paper mode) on real PG."""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

import app.reconcile.service as reconcile_service
from app.alerts.models import Alert, AlertKind
from app.broker.port import OrderRequest
from app.cards.models import Card, CardState, CardType
from app.db import create_session_factory
from app.decide.risk_snapshot import build_risk_snapshot
from app.execution import repo as orders_repo
from app.execution.consumer import BrokerEventConsumer
from app.execution.models import Fill, Order, OrderEvent, OrderKind, OrderType, Side, TimeInForce
from app.execution.submit import EntriesBlocked
from app.instruments import Stock, broker_symbol
from app.ledger.models import Lot, LotChange, LotEvent, LotStatus
from app.reconcile.service import Mismatch, Reconciler
from app.runtime_config.models import Actor
from app.runtime_config.schema import TradingMode
from app.system.flags import FlagService
from app.system.models import FlagName
from tests.integration.conftest import _url
from tests.integration.execution.test_submit_order import GOOGL, World, restarted
from tests.integration.pipeline.harness import Factory, Harness, option_entry
from tests.messages import PROVIDER_A

SPY = Stock(symbol="SPY")
OCC = broker_symbol(GOOGL)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def reconciler(w: World) -> Reconciler:
    return Reconciler(
        w.factory,
        broker=w.broker,
        consumer=BrokerEventConsumer(w.factory, [w.broker]),
        flags=w.flags,
        alerts=w.alerts,
        config=w.config,
        clock=w.clock,
    )


async def world(
    factory: Factory, db_session: AsyncSession, mode: TradingMode = TradingMode.PAPER
) -> World:
    w = World(factory, mode)
    await w.setup(db_session)
    return w


async def count(db: AsyncSession, model: type) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def mismatch_alerts(db: AsyncSession) -> int:
    kind = col(Alert.kind) == AlertKind.RECONCILE_MISMATCH
    return int(await db.scalar(select(func.count()).select_from(Alert).where(kind)) or 0)


async def test_mismatch_blocks_entries_exits_still_work_and_a_fix_clears(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)
    rec = reconciler(w)
    assert (await rec.run()).clean  # broker 2 = lot 2

    w.broker.seed_position(GOOGL, 3, Decimal("1"))
    report = await rec.run()
    assert report.mismatches == (Mismatch(OCC, 3, 2),) and not report.clean
    assert await w.flags.entries_allowed() == (False, "entries_blocked: reconciliation")
    assert await mismatch_alerts(db_session) == 1
    await rec.run()
    assert await mismatch_alerts(db_session) == 1  # the same finding does not alert again

    buy = w.sell(1, kind="entry", side="buy", action_seq=9)
    with pytest.raises(EntriesBlocked):
        await w.service.submit_order(buy, w.approval)
    assert (await w.service.submit_order(w.sell(), w.approval)).status == "working"

    w.broker.seed_position(GOOGL, 2, Decimal("1"))  # fixed at the broker
    assert (await rec.run()).clean
    assert await w.flags.entries_allowed() == (True, None)
    assert rec.last is not None and rec.last.clean


async def test_a_manual_block_is_not_lifted_by_a_clean_run(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)
    await w.flags.set(FlagName.ENTRIES_BLOCKED, True, "operator hold", Actor.USER)
    assert (await reconciler(w).run()).clean
    assert await w.flags.is_active(FlagName.ENTRIES_BLOCKED)


async def test_lot_the_broker_no_longer_holds_is_a_mismatch(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)
    w.broker.seed_position(GOOGL, 0, Decimal("1"))
    report = await reconciler(w).run()
    assert report.mismatches == (Mismatch(OCC, 0, 2),)
    assert await w.flags.is_active(FlagName.ENTRIES_BLOCKED)


async def test_unreadable_broker_blocks_entries(factory: Factory, db_session: AsyncSession) -> None:
    w = await world(factory, db_session)

    async def down() -> list[object]:
        raise ConnectionError("broker down")

    w.broker.positions = down  # type: ignore[method-assign]
    report = await reconciler(w).run()
    assert not report.clean and report.error is not None and "broker down" in report.error
    assert await w.flags.is_active(FlagName.ENTRIES_BLOCKED)


async def test_broker_position_without_lots_becomes_a_counted_foreign_lot(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)
    rec = reconciler(w)
    w.broker.seed_position(SPY, 5, Decimal("400"))
    report = await rec.run()
    assert report.clean and len(report.foreign) == 1
    assert not await w.flags.is_active(FlagName.ENTRIES_BLOCKED)  # not a mismatch
    lot = await db_session.scalar(select(Lot).where(col(Lot.broker_symbol) == "SPY"))
    assert lot is not None and (lot.is_foreign, lot.qty, lot.avg_price) == (True, 5, Decimal("400"))
    risk = await build_risk_snapshot(w.factory, w.md, w.clock.now())
    assert risk.open_positions == 2 and risk.exposure == Decimal("2200")  # 200 tracked + 2000

    assert (await rec.run()).foreign == ()  # idempotent
    w.broker.seed_position(SPY, 8, Decimal("410"))
    await rec.run()
    await db_session.refresh(lot)
    assert (lot.qty, lot.avg_price) == (8, Decimal("410"))
    w.broker.seed_position(SPY, 0, Decimal("410"))
    await rec.run()
    await db_session.refresh(lot)
    assert (lot.qty, lot.status) == (0, LotStatus.CLOSED)
    changes = (
        await db_session.scalars(select(LotEvent.change).where(col(LotEvent.lot_id) == lot.lot_id))
    ).all()
    assert changes == [
        LotChange.ADOPT_FOREIGN,
        LotChange.RECONCILE_ADJUST,
        LotChange.RECONCILE_ADJUST,
    ]


async def test_unknown_cta_order_alerts_and_blocks_but_is_never_recreated(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)
    for coid in ("cta-ghost", "manual-1"):
        await w.broker.submit(
            OrderRequest(coid, GOOGL, Side.BUY, 1, OrderType.LIMIT, TimeInForce.DAY, Decimal("0.5"))
        )
    report = await reconciler(w).run()
    assert report.unknown_orders == ("cta-ghost",) and not report.clean
    assert await w.flags.is_active(FlagName.ENTRIES_BLOCKED)
    assert await mismatch_alerts(db_session) == 1
    assert await count(db_session, Order) == 0


async def test_working_order_the_broker_filled_is_applied_once(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)
    rec = reconciler(w)
    order = await w.service.submit_order(w.sell(), w.approval)
    assert order.broker_order_id is not None and order.status == "working"
    w.broker.fill(order.broker_order_id)
    late = w.broker.pending_events()  # the stream never delivered it (crash / dropped connection)

    report = await rec.run()
    assert report.clean and report.orders_synced == 1  # lot 2 - 1 = broker 1
    db_session.expire_all()
    stored = await db_session.get(Order, order.order_id)
    assert stored is not None and (stored.status, stored.filled_qty) == ("filled", 1)
    lot = await db_session.get(Lot, w.lot_id)
    assert lot is not None and lot.qty == 1
    sources = (
        await db_session.scalars(select(OrderEvent.source).order_by(col(OrderEvent.id)))
    ).all()
    assert sources[-1] == "reconcile"
    assert await count(db_session, Fill) == 1

    assert (await rec.run()).orders_synced == 0  # a repeat run changes nothing
    db_session.expire_all()
    assert await count(db_session, Fill) == 1
    lot = await db_session.get(Lot, w.lot_id)
    assert lot is not None and lot.qty == 1

    for event in late:  # the stream's own fill arrives after all: never past the order's qty
        await rec._consumer.handle(event)
    db_session.expire_all()
    assert await count(db_session, Fill) == 1
    lot = await db_session.get(Lot, w.lot_id)
    assert lot is not None and lot.qty == 1


async def test_orphan_intent_the_broker_never_saw_is_voided(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)

    async def died(*_a: object) -> Order:
        raise RuntimeError("process died")

    w.service._send = died  # type: ignore[method-assign,assignment]  # dies after the intent
    with pytest.raises(RuntimeError):
        await w.service.submit_order(w.sell(), w.approval)
    report = await reconciler(w).run()  # startup: nothing in flight
    assert report.clean and report.orders_synced == 1
    order = await db_session.scalar(select(Order))
    assert order is not None and order.status == "rejected"
    kinds = (await db_session.scalars(select(Alert.kind))).all()
    assert kinds == [AlertKind.ORDER_FAILED]
    retried = await restarted(w).submit_order(w.sell(), w.approval)
    assert retried.status == "rejected"  # the retry sees the void, never a bare intent
    assert "submit" not in [c for c, _ in w.broker.calls]


async def test_open_exit_roots_are_found_for_restart(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session)
    exit_order = await w.service.submit_order(w.sell(), w.approval)
    await w.service.submit_order(
        w.sell(1, "stop", order_type="stop", stop_price=Decimal("0.5"), action_seq=3), w.approval
    )
    async with factory() as db:
        assert await orders_repo.open_exit_roots(db) == [exit_order.order_id]
    assert exit_order.kind == OrderKind.EXIT


async def test_shadow_mode_is_skipped_and_treated_as_clean(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = await world(factory, db_session, TradingMode.SHADOW)
    await w.flags.set(FlagName.ENTRIES_BLOCKED, True, "startup", Actor.SYSTEM)
    w.broker.seed_position(GOOGL, 5, Decimal("1"))  # would be a mismatch at a real broker
    report = await reconciler(w).run()
    assert report.skipped == "shadow" and report.clean
    assert not await w.flags.is_active(FlagName.ENTRIES_BLOCKED)
    assert w.broker.calls == []


async def test_fills_trigger_one_debounced_run(
    factory: Factory, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reconcile_service, "AFTER_FILL_DEBOUNCE_S", 0.01)
    w = await world(factory, db_session)
    rec = reconciler(w)
    await rec.on_fill(None)  # type: ignore[arg-type]
    await rec.on_fill(None)  # type: ignore[arg-type]
    assert rec.last is None
    await asyncio.sleep(0.3)
    assert rec.last is not None and rec.last.clean


async def test_entry_card_is_disabled_while_entries_are_blocked(
    factory: Factory, tmp_path: Path
) -> None:
    h = await Harness.create(factory, tmp_path)
    flags = FlagService(factory, h.alerts)
    await flags.set(
        FlagName.ENTRIES_BLOCKED, True, "reconciliation", Actor.SYSTEM
    )  # as the reconciler does
    env = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )
    await h.process(env)
    async with factory() as db:
        card = await db.scalar(select(Card))
    assert card is not None and card.card_type == CardType.DISABLED
    assert card.state == CardState.DISABLED
    assert card.disabled_reason == "entries blocked: reconciliation mismatch"
