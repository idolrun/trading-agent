"""F051 contract suite against the Alpaca PAPER account. Needs APCA_API_KEY and
APCA_SECRET_KEY (paper keys) in the repo-root .env.
Run: pytest -m live tests/live/test_alpaca.py
Orders are placed far from the market and cancelled; nothing is left working."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from app.alpaca_api import AlpacaApi
from app.broker.alpaca import AlpacaBroker
from app.broker.errors import BrokerRejected
from app.broker.port import BrokerEvent, OrderRequest
from app.config import get_settings
from app.execution.models import OrderStatus, OrderType, Side, TimeInForce
from app.instruments import OptionContract, Stock
from app.marketdata.alpaca import AlpacaMarketData
from app.marketdata.port import QuoteUnavailable

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Overrides the live conftest's database cleanup: these tests never touch Postgres."""


@pytest.fixture
async def api() -> AsyncIterator[AlpacaApi]:
    settings = get_settings()
    if not settings.alpaca_api_key or settings.alpaca_secret_key is None:
        pytest.skip("APCA_API_KEY / APCA_SECRET_KEY not set")
    assert "paper-api" in settings.alpaca_endpoint, "contract tests place orders: paper only"
    client = AlpacaApi(
        settings.alpaca_api_key,
        settings.alpaca_secret_key.get_secret_value(),
        settings.alpaca_endpoint,
    )
    yield client
    await client.aclose()


@pytest.fixture
def broker(api: AlpacaApi) -> AlpacaBroker:
    return AlpacaBroker(api)


@pytest.fixture
def data(api: AlpacaApi) -> AlpacaMarketData:
    settings = get_settings()
    return AlpacaMarketData(
        api, stock_feed=settings.alpaca_stock_feed, options_feed=settings.alpaca_options_feed
    )


async def test_account_and_positions(broker: AlpacaBroker) -> None:
    assert (await broker.account()).equity > 0
    await broker.positions()


async def test_stock_quote_and_unknown_symbol(data: AlpacaMarketData) -> None:
    quote = await data.quote(Stock(symbol="SPY"))
    assert quote.last is not None or quote.bid is not None
    with pytest.raises(QuoteUnavailable):
        await data.quote(Stock(symbol="ZZZZZZ"))


async def test_spx_is_not_available(data: AlpacaMarketData) -> None:
    """Records the accepted gap: Alpaca has no index quote and no SPXW options."""
    with pytest.raises(QuoteUnavailable):
        await data.underlying_price("SPX")
    assert "SPXW" not in await data.optionable_symbols()


async def test_optionable_expiries_chain_and_option_quote(data: AlpacaMarketData) -> None:
    assert "SPY" in await data.optionable_symbols()
    expiry = (await data.expiries("SPY"))[0]
    chain = await data.chain("SPY", expiry)
    assert chain and all(c.underlying == "SPY" and c.expiry == expiry for c, _ in chain)
    assert (await data.quote(chain[0][0])).at is not None


async def test_order_lifecycle_idempotent_submit_lookup_cancel_stream(
    broker: AlpacaBroker,
) -> None:
    events = broker.events()
    waiting = asyncio.ensure_future(anext(events))
    await asyncio.sleep(2)  # let the stream connect, authenticate and listen
    request = OrderRequest(
        client_order_id=f"cta-{uuid.uuid4()}",
        instrument=Stock(symbol="SPY"),
        side=Side.BUY,
        qty=1,
        order_type=OrderType.LIMIT,
        tif=TimeInForce.DAY,
        limit_price=Decimal("1.00"),  # far below the market: rests
    )
    placed = await broker.submit(request)
    assert placed.status in (OrderStatus.SUBMITTED, OrderStatus.WORKING)
    assert (await broker.submit(request)).broker_order_id == placed.broker_order_id  # no duplicate
    found = await broker.get_by_client_id(request.client_order_id)
    assert found is not None and found.broker_order_id == placed.broker_order_id
    assert placed.broker_order_id in [o.broker_order_id for o in await broker.open_orders()]
    event = await asyncio.wait_for(waiting, 10)  # the "new" update for this order
    assert event.client_order_id == request.client_order_id
    await broker.cancel(placed.broker_order_id)
    await broker.cancel(
        placed.broker_order_id
    )  # Alpaca paper: cancelling a cancelled order is a no-op
    with pytest.raises(BrokerRejected):
        await broker.cancel(str(uuid.uuid4()))  # an unknown order is refused
    await events.aclose()  # type: ignore[attr-defined]


async def test_option_limit_order_rests_and_cancels(
    broker: AlpacaBroker, data: AlpacaMarketData
) -> None:
    expiry = (await data.expiries("SPY"))[-1]
    contract = next(c for c, _ in await data.chain("SPY", expiry) if isinstance(c, OptionContract))
    placed = await broker.submit(
        OrderRequest(
            client_order_id=f"cta-{uuid.uuid4()}",
            instrument=contract,
            side=Side.BUY,
            qty=1,
            order_type=OrderType.LIMIT,
            tif=TimeInForce.DAY,
            limit_price=Decimal("0.05"),
        )
    )
    assert placed.instrument == contract
    await broker.cancel(placed.broker_order_id)


async def test_market_buy_fills_with_an_execution_id_then_flattens(
    api: AlpacaApi, broker: AlpacaBroker
) -> None:
    """Market hours only: fills need a market. Buys one SPY share, checks the fill, sells it."""
    if not (await api.get("/v2/clock"))["is_open"]:
        pytest.skip("market closed: paper orders do not fill")
    events = broker.events()
    await asyncio.sleep(2)  # let the stream connect, authenticate and listen

    async def trade(side: Side) -> tuple[str, list[BrokerEvent]]:
        request = OrderRequest(
            client_order_id=f"cta-{uuid.uuid4()}",
            instrument=Stock(symbol="SPY"),
            side=side,
            qty=1,
            order_type=OrderType.LIMIT,
            tif=TimeInForce.DAY,
            # marketable: well through the market on the right side
            limit_price=Decimal("1000") if side == Side.BUY else Decimal("1.00"),
        )
        await broker.submit(request)
        seen: list[BrokerEvent] = []
        async with asyncio.timeout(30):
            while not any(e.kind == "fill" for e in seen):
                event = await anext(events)
                if event.client_order_id == request.client_order_id:
                    seen.append(event)
        return request.client_order_id, seen

    try:
        _, bought = await trade(Side.BUY)
        fill = next(e for e in bought if e.kind == "fill")
        assert fill.fill_id and fill.qty == 1 and fill.price is not None
        assert [p.qty for p in await broker.positions() if p.instrument == Stock(symbol="SPY")] == [
            1
        ]
    finally:
        await trade(Side.SELL)  # flatten even if an assertion failed
        await events.aclose()  # type: ignore[attr-defined]
