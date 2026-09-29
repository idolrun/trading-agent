"""F040: broker status / fill intake -> orders + lot ledger, exactly once (FakeBroker, paper)."""

import logging
import random
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from itertools import count
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.alerts.service import AlertService
from app.broker.fake import FakeBroker
from app.broker.port import BrokerEvent
from app.broker.simulated import SimulatedBroker
from app.cards import repo as cards
from app.cards.models import Approval, Card, CardAction, CardSource, CardState, CardType
from app.db import in_transaction
from app.execution.consumer import BrokerEventConsumer
from app.execution.fills import Applied, next_status
from app.execution.intent import Approved, OrderIntent
from app.execution.models import (
    Fill,
    Order,
    OrderKind,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from app.execution.submit import OrderService
from app.ledger import repo as ledger
from app.ledger.models import Lot, LotEvent, LotStatus
from app.ledger.positions import apply_to_position
from app.marketdata.fake import FakeMarketData
from app.markettime import FixedClock
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig, TradingMode
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.validate.normalize import NormalizedSignal
from tests.integration.execution.test_submit_order import GOOGL, NOW, factory  # noqa: F401
from tests.integration.pipeline.harness import Factory
from tests.integration.schema.helpers import add_session, add_signal

PROVIDER = 7
_messages = count(5000)


@dataclass
class Books:
    factory: Factory
    broker: FakeBroker
    orders: OrderService
    consumer: BrokerEventConsumer
    session_id: uuid.UUID
    db: AsyncSession
    hooked: list[Applied]

    async def position(self) -> uuid.UUID:
        sig = await add_signal(self.db, provider_id=PROVIDER, message_id=next(_messages))
        entry = NormalizedSignal(
            msg_type="entry",
            instrument="option",
            underlying="GOOGL",
            expiry=GOOGL.expiry,
            strike=GOOGL.strike,
            right="call",
        )
        update = apply_to_position(None, entry)
        assert update is not None
        async with in_transaction(self.factory, None) as db:
            row = await ledger.record(
                db, provider_id=PROVIDER, row=None, update=update, signal_id=sig.signal_id
            )
        return row.provider_position_id

    async def order(
        self,
        position_id: uuid.UUID,
        qty: int,
        *,
        side: Side = Side.BUY,
        lot: uuid.UUID | None = None,
    ) -> Order:
        """An approved card + its order, resting at the broker (working, not filled)."""
        action = CardAction.ENTRY if side == Side.BUY else CardAction.EXIT
        key = f"test:{uuid.uuid4()}"
        async with in_transaction(self.factory, None) as db:
            card = Card(
                card_id=cards.card_id_for(key),
                dedupe_key=key,
                source=CardSource.SYSTEM,
                action=action,
                card_type=CardType.NORMAL,
                state=CardState.PENDING,
                provider_id=PROVIDER,
                provider_position_id=position_id,
                lot_id=lot,
                proposal={},
                posted_at=NOW,
            )
            await cards.insert(db, card)
            approval = Approval(
                card_id=card.card_id,
                decision="approve",
                approved_proposal={},
                card_version=1,
                session_id=self.session_id,
            )
            db.add(approval)
        intent = OrderIntent(
            card_id=card.card_id,
            kind=OrderKind.ENTRY if side == Side.BUY else OrderKind.EXIT,
            side=side,
            instrument=GOOGL,
            qty=qty,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("1.00"),
            tif=TimeInForce.DAY,
            provider_position_id=position_id,
            lot_id=lot,
        )
        return await self.orders.submit_order(intent, Approved(approval.approval_id))

    async def drain(self) -> None:
        for event in self.broker.pending_events():
            await self.consumer.handle(event)

    async def get(self, order_id: uuid.UUID) -> Order:
        async with self.factory() as db:
            order = await db.get(Order, order_id)
        assert order is not None
        return order

    async def lot_of(self, order_id: uuid.UUID) -> Lot:
        order = await self.get(order_id)
        assert order.lot_id is not None, "fill did not link the lot to the order"
        async with self.factory() as db:
            lot = await db.get(Lot, order.lot_id)
        assert lot is not None
        return lot


@pytest.fixture
async def b(factory: Factory, db_session: AsyncSession) -> AsyncIterator[Books]:  # noqa: F811
    clock = FixedClock(NOW)
    broker = FakeBroker(clock)
    alerts = AlertService(factory)
    config = ConfigService(factory)
    await config.update(
        RuntimeConfig(trading_mode=TradingMode.PAPER),
        expected_version=0,
        actor=Actor.CLI,
        reason="t",
    )
    orders = OrderService(
        factory,
        broker=broker,
        simulated=SimulatedBroker(FakeMarketData(clock), clock),
        flags=FlagService(factory, alerts),
        alerts=alerts,
        config=config,
        clock=clock,
    )
    consumer = BrokerEventConsumer(factory, [broker])
    hooked: list[Applied] = []

    async def hook(applied: Applied) -> None:
        hooked.append(applied)

    consumer.on_fill.append(hook)
    yield Books(
        factory, broker, orders, consumer, await add_session(db_session), db_session, hooked
    )


async def test_entry_fill_creates_the_lot_linked_to_the_position(b: Books) -> None:
    position = await b.position()
    order = await b.order(position, 4)
    b.broker.fill(order.broker_order_id or "", price=Decimal("0.70"))
    await b.drain()
    lot = await b.lot_of(order.order_id)
    assert lot.provider_position_id == position and lot.qty == 4
    assert lot.avg_price == Decimal("0.70") and lot.status == LotStatus.OPEN
    got = await b.get(order.order_id)
    assert got.status == OrderStatus.FILLED and got.filled_qty == 4
    assert got.avg_fill_price == Decimal("0.70")
    async with b.factory() as db:
        [event] = (
            await db.scalars(select(LotEvent).where(col(LotEvent.lot_id) == lot.lot_id))
        ).all()
    assert event.fill_id is not None
    assert [a.order.order_id for a in b.hooked] == [order.order_id]  # on_fill after commit


async def test_submit_and_fill_lines_carry_the_client_order_id(
    b: Books, log_lines: list[dict[str, Any]]
) -> None:
    log, submit = logging.getLogger("test"), b.broker.submit

    async def logged(request: Any) -> Any:
        log.info("broker submit")
        return await submit(request)

    async def on_fill(applied: Applied) -> None:
        log.info("fill hook")

    b.broker.submit = logged  # type: ignore[method-assign]
    b.consumer.on_fill.append(on_fill)
    order = await b.order(await b.position(), 2)
    b.broker.fill(order.broker_order_id or "", price=Decimal("0.70"))
    await b.drain()
    seen = {x["msg"]: x["client_order_id"] for x in log_lines if x["logger"] == "test"}
    assert seen == {"broker submit": order.client_order_id, "fill hook": order.client_order_id}


async def test_add_fill_grows_the_lot_and_recomputes_the_average(b: Books) -> None:
    position = await b.position()
    first = await b.order(position, 2)
    b.broker.fill(first.broker_order_id or "", price=Decimal("1.00"))
    await b.drain()
    lot = await b.lot_of(first.order_id)
    add = await b.order(position, 2, lot=lot.lot_id)
    b.broker.fill(add.broker_order_id or "", price=Decimal("2.00"))
    await b.drain()
    grown = await b.lot_of(add.order_id)
    assert grown.lot_id == lot.lot_id and grown.qty == 4 and grown.avg_price == Decimal("1.5")


async def test_duplicate_fill_event_changes_nothing(b: Books) -> None:
    position = await b.position()
    order = await b.order(position, 3)
    b.broker.fill(order.broker_order_id or "", price=Decimal("0.50"))
    events = b.broker.pending_events()
    for event in [*events, *events]:  # stream + poll delivery of the same fill
        await b.consumer.handle(event)
    lot = await b.lot_of(order.order_id)
    assert lot.qty == 3
    async with b.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Fill)) == 1


async def test_partial_fill_then_cancel_keeps_the_partial(b: Books) -> None:
    position = await b.position()
    order = await b.order(position, 5)
    b.broker.fill(order.broker_order_id or "", qty=2, price=Decimal("0.80"))
    await b.drain()
    await b.orders.cancel_order(order.order_id, "entry window over")
    await b.drain()
    got = await b.get(order.order_id)
    assert got.status == OrderStatus.CANCELLED and got.filled_qty == 2
    assert (await b.lot_of(order.order_id)).qty == 2


async def test_sell_fill_reduces_and_closes_the_lot(b: Books) -> None:
    position = await b.position()
    entry = await b.order(position, 2)
    b.broker.fill(entry.broker_order_id or "", price=Decimal("1.00"))
    await b.drain()
    lot = await b.lot_of(entry.order_id)
    sell = await b.order(position, 2, side=Side.SELL, lot=lot.lot_id)
    b.broker.fill(sell.broker_order_id or "", price=Decimal("1.40"))
    await b.drain()
    closed = await b.lot_of(sell.order_id)
    assert closed.lot_id == lot.lot_id and closed.qty == 0 and closed.status == LotStatus.CLOSED


async def test_late_and_repeated_status_events_never_regress(b: Books) -> None:
    position = await b.position()
    order = await b.order(position, 1)
    assert order.broker_order_id is not None
    b.broker.fill(order.broker_order_id, price=Decimal("0.60"))
    await b.drain()
    stale = BrokerEvent(
        kind="status",
        broker_order_id=order.broker_order_id,
        client_order_id=order.client_order_id,
        status=OrderStatus.WORKING,
        at=NOW,
    )
    await b.consumer.handle(stale)
    assert (await b.get(order.order_id)).status == OrderStatus.FILLED
    assert next_status(OrderStatus.WORKING, OrderStatus.SUBMITTED) is None
    assert next_status(OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELLED) == OrderStatus.CANCELLED
    assert next_status(OrderStatus.CANCELLED, OrderStatus.FILLED) is None


async def test_unknown_order_events_are_ignored(b: Books) -> None:
    event = BrokerEvent(
        kind="fill",
        broker_order_id="B-foreign",
        client_order_id="not-ours",
        status=OrderStatus.FILLED,
        at=NOW,
        fill_id="F-foreign",
        qty=1,
        price=Decimal(1),
    )
    assert await b.consumer.handle(event) is None
    async with b.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Fill)) == 0


async def test_lot_always_equals_the_sum_of_unique_fills(b: Books) -> None:
    """Property over random streams: partial fills in random sizes, delivered shuffled and
    with duplicates -> lot qty == order filled qty == sum of distinct fills."""
    rng = random.Random(40)  # noqa: S311 — reproducible test data
    for _ in range(15):
        position = await b.position()
        qty = rng.randint(1, 12)
        order = await b.order(position, qty)
        assert order.broker_order_id is not None
        remaining = rng.randint(1, qty)
        while remaining:
            size = rng.randint(1, remaining)
            b.broker.fill(order.broker_order_id, qty=size, price=Decimal(rng.randint(10, 99)) / 100)
            remaining -= size
        events = b.broker.pending_events()
        stream = events + rng.sample(events, k=rng.randint(0, len(events)))
        rng.shuffle(stream)
        for event in stream:
            await b.consumer.handle(event)
        unique = sum(e.qty or 0 for e in events if e.kind == "fill")
        lot = await b.lot_of(order.order_id)
        got = await b.get(order.order_id)
        assert lot.qty == got.filled_qty == unique
        fills = [e for e in events if e.kind == "fill"]
        notional = sum((e.qty or 0) * (e.price or 0) for e in fills)
        assert got.avg_fill_price == (Decimal(notional) / unique).quantize(Decimal("0.0001"))
