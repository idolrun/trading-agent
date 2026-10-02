import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.alpaca_api import AlpacaApi, alpaca_symbol, instrument_of
from app.broker.alpaca import AlpacaBroker
from app.broker.errors import BrokerRejected, BrokerTimeout, BrokerUnavailable
from app.broker.port import BrokerClient, BrokerEvents, OcoRequest, OrderRequest
from app.execution.models import OrderStatus, OrderType, Side, TimeInForce
from app.instruments import OptionContract, Stock

COID = "cta-00000000-0000-0000-0000-000000000001"
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)
OCC = "GOOGL261002C00357500"


class Alpaca:
    """Canned Alpaca: records requests, answers by "METHOD path"."""

    def __init__(self, **routes: Any) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(f"{request.method} {request.url.path}")
        if callable(answer):
            answer = answer(request)
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    def body(self) -> dict[str, Any]:
        return json.loads([r for r in self.requests if r.method == "POST"][-1].content)


class Socket:
    """One WebSocket connection: replays `frames`, then ends (or raises `error`)."""

    def __init__(self, frames: list[Any], error: Exception | None = None) -> None:
        self.frames, self.error, self.sent = frames, error, []

    async def __aenter__(self) -> "Socket":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self) -> "Socket":
        self._it = iter(self.frames)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            if self.error:
                raise self.error from None
            raise StopAsyncIteration from None


def broker(fake: Alpaca, sockets: list[Socket] | None = None) -> AlpacaBroker:
    api = AlpacaApi("key", "secret", transport=httpx.MockTransport(fake))
    queue = iter(sockets or [])
    return AlpacaBroker(api, connect=lambda _url: next(queue), reconnect_s=0)  # type: ignore[arg-type,return-value]


def order(inst: Any = GOOGL, side: Side = Side.BUY, **kw: Any) -> OrderRequest:
    return OrderRequest(
        client_order_id=COID,
        instrument=inst,
        side=side,
        qty=kw.pop("qty", 2),
        order_type=kw.pop("order_type", OrderType.LIMIT),
        tif=kw.pop("tif", TimeInForce.DAY),
        limit_price=kw.pop("limit_price", Decimal("1.67")),
        stop_price=kw.pop("stop_price", None),
    )


def raw(id: str = "o-1", status: str = "new", **kw: Any) -> dict[str, Any]:
    return {
        "id": id,
        "client_order_id": COID,
        "symbol": OCC,
        "side": "buy",
        "qty": "2",
        "filled_qty": "0",
        "status": status,
        **kw,
    }


def update(event: str, **kw: Any) -> str:
    order_status = {"fill": "filled", "partial_fill": "partially_filled"}.get(event, event)
    data = {
        "event": event,
        "timestamp": "2026-10-01T15:00:00.123456789Z",
        "order": raw(status=order_status, filled_qty="1" if "fill" in event else "0"),
        **kw,
    }
    return json.dumps({"stream": "trade_updates", "data": data})


def test_satisfies_ports() -> None:
    client: BrokerClient = broker(Alpaca())
    events: BrokerEvents = broker(Alpaca())
    assert client and events


def test_option_symbols_drop_the_occ_root_padding() -> None:
    assert alpaca_symbol(GOOGL) == OCC
    assert instrument_of(OCC) == GOOGL
    assert instrument_of("SPY") == Stock(symbol="SPY")


async def test_submit_sends_an_option_order_with_our_client_id() -> None:
    fake = Alpaca(**{"POST /v2/orders": raw()})
    placed = await broker(fake).submit(order())
    assert (placed.broker_order_id, placed.status, placed.client_order_id, placed.instrument) == (
        "o-1",
        OrderStatus.SUBMITTED,
        COID,
        GOOGL,
    )
    assert fake.body() == {
        "symbol": OCC,
        "qty": "2",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "client_order_id": COID,
        "limit_price": "1.67",
        "position_intent": "buy_to_open",
    }
    assert fake.requests[0].headers["APCA-API-KEY-ID"] == "key"


async def test_submit_stop_sell_gtc_stock_and_option() -> None:
    fake = Alpaca(**{"POST /v2/orders": raw()})
    stop = {"order_type": OrderType.STOP, "limit_price": None, "stop_price": Decimal("1.2")}
    await broker(fake).submit(order(GOOGL, Side.SELL, tif=TimeInForce.GTC, **stop))
    body = fake.body()
    assert (body["type"], body["time_in_force"], body["stop_price"]) == ("stop", "gtc", "1.2")
    assert (body["position_intent"], "limit_price" in body) == ("sell_to_close", False)
    stock = {"order_type": OrderType.STOP_MARKET, "limit_price": None, "stop_price": Decimal("98")}
    await broker(fake).submit(order(Stock(symbol="SPY"), Side.SELL, **stock))
    assert "position_intent" not in fake.body() and fake.body()["type"] == "stop"


async def test_a_retried_client_id_returns_the_existing_order() -> None:
    fake = Alpaca(
        **{
            "POST /v2/orders": httpx.Response(
                422, json={"code": 40010001, "message": "client_order_id must be unique"}
            ),
            "GET /v2/orders:by_client_order_id": raw(
                "o-9", "filled", filled_qty="2", filled_avg_price="1.6"
            ),
        }
    )
    placed = await broker(fake).submit(order())
    assert (placed.broker_order_id, placed.filled_qty, placed.avg_fill_price) == (
        "o-9",
        2,
        Decimal("1.6"),
    )


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(422, json={"message": "invalid qty"}), BrokerRejected),
        (httpx.Response(403, json={"message": "insufficient buying power"}), BrokerRejected),
        (httpx.Response(403, json={"message": "forbidden."}), BrokerUnavailable),
        (httpx.Response(401, json={"message": "unauthorized."}), BrokerUnavailable),
        (httpx.Response(429), BrokerUnavailable),
        (httpx.Response(503), BrokerTimeout),
    ],
)
async def test_submit_failures_are_classified_by_whether_the_order_can_exist(
    answer: httpx.Response, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await broker(Alpaca(**{"POST /v2/orders": answer})).submit(order())


async def test_lookup_open_orders_cancel() -> None:
    fake = Alpaca(
        **{
            "GET /v2/orders:by_client_order_id": lambda r: (
                raw()
                if r.url.params["client_order_id"] == COID
                else httpx.Response(404, json={"message": "order not found"})
            ),
            "GET /v2/orders": [
                raw("o-1"),
                raw("o-2", "partially_filled", filled_qty="1", filled_avg_price="1.5"),
            ],
            "DELETE /v2/orders/o-1": httpx.Response(204),
            "DELETE /v2/orders/o-3": httpx.Response(
                422, json={"message": "order is not cancelable"}
            ),
        }
    )
    b = broker(fake)
    assert (await b.get_by_client_id(COID)).broker_order_id == "o-1"  # type: ignore[union-attr]
    assert await b.get_by_client_id("cta-missing") is None
    assert [(o.broker_order_id, o.status) for o in await b.open_orders()] == [
        ("o-1", OrderStatus.SUBMITTED),
        ("o-2", OrderStatus.PARTIALLY_FILLED),
    ]
    assert fake.requests[-1].url.params["status"] == "open"
    await b.cancel("o-1")
    with pytest.raises(BrokerRejected, match="not cancelable"):
        await b.cancel("o-3")


async def test_positions_and_account() -> None:
    positions = [
        {"symbol": OCC, "qty": "2", "avg_entry_price": "1.67", "asset_class": "us_option"},
        {"symbol": "SPY", "qty": "10", "avg_entry_price": "450.5"},
        {"symbol": "BTCUSD", "qty": "0.4", "avg_entry_price": "60000"},
    ]
    account = {"equity": "100000.5", "buying_power": "200000", "options_buying_power": "100000"}
    b = broker(Alpaca(**{"GET /v2/positions": positions, "GET /v2/account": account}))
    assert [(p.instrument, p.qty, p.avg_price) for p in await b.positions()] == [
        (GOOGL, 2, Decimal("1.67")),
        (Stock(symbol="SPY"), 10, Decimal("450.5")),
    ]
    got = await b.account()
    assert (got.equity, got.buying_power) == (Decimal("100000.5"), Decimal(200000))


async def test_native_oco_is_refused() -> None:
    with pytest.raises(BrokerRejected):
        await broker(Alpaca()).submit_oco(OcoRequest("cta-grp", [order(), order()]))


async def test_events_authenticate_listen_and_translate_updates() -> None:
    socket = Socket(
        [
            json.dumps({"stream": "authorization", "data": {"status": "authorized"}}),
            json.dumps({"stream": "listening", "data": {"streams": ["trade_updates"]}}),
            update("new"),
            update("partial_fill", execution_id="x-1", price="1.5", qty="1"),
            update("fill", execution_id="x-2", price="1.7", qty="1").encode(),  # bytes frames too
            update("canceled"),
        ]
    )
    stream = broker(Alpaca(), [socket]).events()
    got = [await anext(stream) for _ in range(4)]
    await stream.aclose()
    assert [m["action"] for m in socket.sent] == ["auth", "listen"]
    assert socket.sent[0]["secret"] == "secret"
    assert [(e.kind, e.status, e.fill_id, e.qty, e.price) for e in got] == [
        ("status", OrderStatus.SUBMITTED, None, None, None),
        ("fill", OrderStatus.PARTIALLY_FILLED, "x-1", 1, Decimal("1.5")),
        ("fill", OrderStatus.FILLED, "x-2", 1, Decimal("1.7")),
        ("status", OrderStatus.CANCELLED, None, None, None),
    ]
    assert got[0].at.year == 2026 and got[0].client_order_id == COID


async def test_events_reconnect_after_a_drop_and_a_failed_authorization() -> None:
    denied = json.dumps({"stream": "authorization", "data": {"status": "unauthorized"}})
    sockets = [
        Socket([denied]),
        Socket([], error=ConnectionResetError("dropped")),
        Socket([update("new")]),
    ]
    stream = broker(Alpaca(), sockets).events()
    event = await anext(stream)
    await stream.aclose()
    assert (event.kind, event.status) == ("status", OrderStatus.SUBMITTED)


async def test_cancel_of_an_order_that_just_filled_is_not_an_error() -> None:
    not_cancelable = httpx.Response(422, json={"message": "order is not cancelable"})
    fake = Alpaca(
        **{
            "DELETE /v2/orders/o-1": not_cancelable,
            "GET /v2/orders/o-1": raw("o-1", "filled", filled_qty="2", filled_avg_price="1.6"),
            "DELETE /v2/orders/o-2": not_cancelable,
            "GET /v2/orders/o-2": raw("o-2", "new"),
        }
    )
    b = broker(fake)
    await b.cancel("o-1")  # done: the fill arrives on the stream
    with pytest.raises(BrokerRejected, match="not cancelable"):
        await b.cancel("o-2")  # still working: a real refusal


async def test_rows_that_cannot_be_modelled_are_skipped_not_fatal() -> None:
    crypto = {"id": "o-9", "symbol": "BTC/USD", "side": "buy", "qty": "1", "status": "new"}
    fake = Alpaca(
        **{
            "GET /v2/orders": [crypto, raw("o-1")],
            "GET /v2/positions": [
                {"symbol": "BTC/USD", "qty": "1", "avg_entry_price": "60000"},
                {"symbol": "SPY", "qty": "3", "avg_entry_price": "450"},
            ],
        }
    )
    b = broker(fake)
    assert [o.broker_order_id for o in await b.open_orders()] == ["o-1"]
    assert [p.instrument for p in await b.positions()] == [Stock(symbol="SPY")]


async def test_prices_are_sent_in_plain_notation() -> None:
    fake = Alpaca(**{"POST /v2/orders": raw()})
    await broker(fake).submit(order(Stock(symbol="SPY"), limit_price=Decimal("1E+2")))
    assert fake.body()["limit_price"] == "100"


async def test_an_unreadable_update_does_not_drop_the_stream() -> None:
    bad = json.dumps({"stream": "trade_updates", "data": {"event": "fill", "order": raw()}})
    odd = json.dumps(
        {"stream": "trade_updates", "data": {"event": "new", "order": raw(symbol="BTC/USD")}}
    )
    stream = broker(Alpaca(), [Socket([bad, odd, update("new")])]).events()
    event = await anext(stream)  # same socket: no reconnect happened
    await stream.aclose()
    assert (event.kind, event.status) == ("status", OrderStatus.SUBMITTED)


async def test_failed_authorization_backs_off_instead_of_hammering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.broker.alpaca.asyncio.sleep", record)
    denied = json.dumps({"stream": "authorization", "data": {"status": "unauthorized"}})
    listening = json.dumps({"stream": "listening", "data": {"streams": ["trade_updates"]}})
    sockets = [
        Socket([denied]),
        Socket([denied]),
        Socket([denied]),
        Socket([listening, update("new")]),
    ]
    queue = iter(sockets)
    api = AlpacaApi("key", "secret", transport=httpx.MockTransport(Alpaca()))
    stream = AlpacaBroker(api, connect=lambda _url: next(queue), reconnect_s=1.0).events()  # type: ignore[arg-type]
    await anext(stream)
    await stream.aclose()  # type: ignore[attr-defined]
    assert sleeps == [1.0, 2.0, 4.0]
