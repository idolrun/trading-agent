from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.broker.errors import BrokerRejected, BrokerTimeout, BrokerUnavailable
from app.broker.fake import Behavior, FakeBroker
from app.broker.port import BrokerClient, BrokerEvents, OcoRequest, OrderRequest
from app.execution.models import OrderStatus, OrderType, Side, TimeInForce
from app.instruments import OptionContract
from app.markettime import FixedClock

GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)


def order(coid: str, side: Side = Side.BUY, qty: int = 2, **kw: object) -> OrderRequest:
    return OrderRequest(
        client_order_id=coid,
        instrument=GOOGL,
        side=side,
        qty=qty,
        order_type=kw.pop("order_type", OrderType.LIMIT),  # type: ignore[arg-type]
        tif=TimeInForce.DAY,
        limit_price=kw.pop("limit_price", Decimal("1.67")),  # type: ignore[arg-type]
        stop_price=kw.pop("stop_price", None),  # type: ignore[arg-type]
    )


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker(FixedClock(datetime(2026, 9, 28, 14, 0, tzinfo=UTC)))


def test_satisfies_ports(broker: FakeBroker) -> None:
    client: BrokerClient = broker
    events: BrokerEvents = broker
    assert client is events


async def test_fill_now_emits_status_and_fill(broker: FakeBroker) -> None:
    broker.script("cta-1", Behavior.FILL_NOW)
    placed = await broker.submit(order("cta-1"))
    assert (placed.status, placed.filled_qty, placed.avg_fill_price) == (
        OrderStatus.FILLED,
        2,
        Decimal("1.67"),
    )
    events = broker.pending_events()
    assert [(e.kind, e.status) for e in events] == [
        ("status", OrderStatus.WORKING),
        ("fill", OrderStatus.FILLED),
    ]
    assert events[1].fill_id and events[1].qty == 2
    [position] = await broker.positions()
    assert (position.qty, position.avg_price) == (2, Decimal("1.67"))


async def test_duplicate_client_order_id_returns_same_order(broker: FakeBroker) -> None:
    broker.script("cta-1", Behavior.FILL_NOW)
    first = await broker.submit(order("cta-1"))
    broker.pending_events()
    again = await broker.submit(order("cta-1"))
    assert again == first
    assert broker.pending_events() == []
    assert [name for name, _ in broker.calls] == ["submit", "submit"]


async def test_partial_then_rest(broker: FakeBroker) -> None:
    broker.script("cta-1", Behavior.PARTIAL, qty=1)
    placed = await broker.submit(order("cta-1", qty=3))
    assert (placed.status, placed.filled_qty) == (OrderStatus.PARTIALLY_FILLED, 1)
    assert await broker.open_orders() == [placed]
    done = broker.fill(placed.broker_order_id, price=Decimal("1.70"))
    assert (done.status, done.filled_qty) == (OrderStatus.FILLED, 3)
    assert done.avg_fill_price == (Decimal("1.67") + 2 * Decimal("1.70")) / 3
    assert await broker.open_orders() == []


async def test_never_fill_and_cancel(broker: FakeBroker) -> None:
    placed = await broker.submit(order("cta-1"))  # default: never fills
    assert placed.status is OrderStatus.WORKING
    await broker.cancel(placed.broker_order_id)
    got = await broker.get_by_client_id("cta-1")
    assert got is not None and got.status is OrderStatus.CANCELLED
    await broker.cancel(placed.broker_order_id)  # terminal: no-op
    with pytest.raises(BrokerRejected):
        await broker.cancel("B-999")


async def test_reject(broker: FakeBroker) -> None:
    broker.script("cta-1", Behavior.REJECT)
    with pytest.raises(BrokerRejected):
        await broker.submit(order("cta-1"))
    got = await broker.get_by_client_id("cta-1")
    assert got is not None and got.status is OrderStatus.REJECTED


async def test_timeout_then_exists(broker: FakeBroker) -> None:
    broker.script("cta-1", Behavior.TIMEOUT_THEN_EXISTS)
    with pytest.raises(BrokerTimeout):
        await broker.submit(order("cta-1"))
    got = await broker.get_by_client_id("cta-1")
    assert got is not None and got.status is OrderStatus.WORKING


async def test_timeout_then_missing_and_unavailable(broker: FakeBroker) -> None:
    broker.script("cta-1", Behavior.TIMEOUT_THEN_MISSING)
    broker.script("cta-2", Behavior.UNAVAILABLE)
    with pytest.raises(BrokerTimeout):
        await broker.submit(order("cta-1"))
    with pytest.raises(BrokerUnavailable):
        await broker.submit(order("cta-2"))
    assert await broker.get_by_client_id("cta-1") is None
    assert await broker.get_by_client_id("cta-2") is None


async def test_oco_fill_cancels_siblings(broker: FakeBroker) -> None:
    broker.script("entry", Behavior.FILL_NOW)
    await broker.submit(order("entry"))
    legs = await broker.submit_oco(
        OcoRequest(
            client_order_id_prefix="cta-oco-1",
            legs=[
                order(
                    "cta-oco-1-stop",
                    Side.SELL,
                    order_type=OrderType.STOP_MARKET,
                    limit_price=None,
                    stop_price=Decimal("1.20"),
                ),
                order("cta-oco-1-tp1", Side.SELL, limit_price=Decimal("2.50")),
            ],
        )
    )
    assert [leg.status for leg in legs] == [OrderStatus.WORKING, OrderStatus.WORKING]
    again = await broker.submit_oco(OcoRequest(client_order_id_prefix="cta-oco-1", legs=[]))
    assert again == legs
    broker.pending_events()

    broker.fill(legs[1].broker_order_id)
    stop = await broker.get_by_client_id("cta-oco-1-stop")
    assert stop is not None and stop.status is OrderStatus.CANCELLED
    assert [(e.kind, e.status) for e in broker.pending_events()] == [
        ("fill", OrderStatus.FILLED),
        ("status", OrderStatus.CANCELLED),
    ]
    assert await broker.positions() == []  # bought 2, sold 2


async def test_events_stream(broker: FakeBroker) -> None:
    broker.script("cta-1", Behavior.FILL_NOW)
    await broker.submit(order("cta-1"))
    stream = broker.events()
    assert (await anext(stream)).kind == "status"
    assert (await anext(stream)).kind == "fill"


async def test_account(broker: FakeBroker) -> None:
    broker.equity = Decimal(25_000)
    assert (await broker.account()).equity == Decimal(25_000)
