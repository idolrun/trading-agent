"""The application path against the Alpaca PAPER account (nothing mocked below the interpreter):

    message -> (recorded interpretation) -> validation + live quote -> Alpaca account + positions
    -> risk -> card -> approve -> revalidation -> `submit_order` -> Alpaca paper order
    -> `trade_updates` fill -> lot -> reconciliation against the broker

Run (regular US market hours, paper keys in the repo-root `.env`, test database up):

    cd backend && uv run pytest -m live tests/live/test_paper_app_flow.py -s

One SPY share is bought and sold again. Skipped outside market hours (paper orders do not fill
then) and without keys. Jev / Laya are not live here: their routing is covered by
`tests/integration/graph/test_paper_flow.py`; the interpretation is recorded.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.alpaca_api import AlpacaApi
from app.broker.alpaca import AlpacaBroker
from app.broker.port import OrderRequest
from app.broker.safety import ALPACA_PAPER, check_mode
from app.cards.models import Card, CardAction, CardState
from app.config import get_settings
from app.db import create_session_factory
from app.execution.consumer import BrokerEventConsumer
from app.execution.models import Order, OrderStatus, OrderType, Side, TimeInForce
from app.graph.checkpointer import open_pool, saver_and_store
from app.instruments import Stock
from app.interpret.signal import ParsedSignal, SignalFields
from app.ledger.models import Lot
from app.main import libpq_dsn
from app.marketdata.alpaca import AlpacaMarketData
from app.markettime import FixedClock
from app.reconcile.service import Reconciler
from app.runtime_config.schema import TradingMode
from app.system.flags import FlagService
from tests.integration.conftest import _url
from tests.integration.graph.conftest import World
from tests.integration.pipeline.harness import Factory, Harness, lit
from tests.integration.schema.helpers import add_session
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.live
D = Decimal


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_signal_to_alpaca_paper_fill_lot_and_reconciliation(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    settings = get_settings()
    if not settings.alpaca_api_key or settings.alpaca_secret_key is None:
        pytest.skip("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    assert "paper-api" in settings.alpaca_endpoint, "places real paper orders: paper only"
    api = AlpacaApi(
        settings.alpaca_api_key,
        settings.alpaca_secret_key.get_secret_value(),
        settings.alpaca_endpoint,
    )
    pool = open_pool(libpq_dsn(_url("test_database_url")))
    consumer_task: asyncio.Task[None] | None = None
    flatten: list[tuple[Stock, int]] = []
    try:
        if not (await api.get("/v2/clock"))["is_open"]:
            pytest.skip("market closed: paper orders do not fill")
        await pool.open()
        saver, _ = saver_and_store(pool)
        broker = AlpacaBroker(api)
        data = AlpacaMarketData(
            api, stock_feed=settings.alpaca_stock_feed, options_feed=settings.alpaca_options_feed
        )
        spy = Stock(symbol="SPY")
        quote = await data.quote(spy)
        price = (quote.ask or quote.last or D(0)).quantize(D("0.01"))
        assert price > 0, "no SPY quote"

        h = await Harness.create(
            factory,
            tmp_path,
            trading_mode=TradingMode.PAPER,
            quote_max_age_s=300,
        )
        h.clock = FixedClock(datetime.now(UTC))
        h.md = data  # type: ignore[assignment]  # the real market data, not the fixtures
        approvals = h.approval_service(saver, broker)  # type: ignore[arg-type]
        orders = approvals._executor._orders
        orders._broker_kind, orders._allow_fake = ALPACA_PAPER, False  # the real safety check
        assert check_mode(TradingMode.PAPER, ALPACA_PAPER) is None
        world = World(
            h,
            broker,
            approvals,
            h.processor(approvals=approvals),
            saver,
            await add_session(db_session),  # type: ignore[arg-type]
        )

        text = f"bought SPY @ {price}"
        signal = ParsedSignal(
            msg_type="stock_entry",
            instrument="stock",
            fields=SignalFields(underlying=lit("SPY"), entry_price=lit(price, str(price))),
        )
        env = await h.message(text, [signal], at=h.clock.now(), author=PROVIDER_A)
        await h.process(env, processor=world.processor)
        async with factory() as db:
            card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
        assert card is not None, "no entry card"
        assert card.state == CardState.PENDING, card.proposal.get("disabled_reason")
        qty = int(card.proposal["qty"])

        consumer = BrokerEventConsumer(factory, [broker])
        if approvals._executor.brackets is not None:
            consumer.on_fill.append(approvals._executor.brackets.on_fill)
        consumer_task = asyncio.create_task(consumer.run())
        await asyncio.sleep(2)  # the order stream connects, authenticates and listens

        pf = await approvals.preflight(card.card_id)
        assert pf.failure is None, pf.checks
        assert pf.buying_power is not None and pf.equity is not None  # the real paper account

        await world.decide(card.card_id)
        assert (await world.card(card.card_id)).state == CardState.SUBMITTED
        flatten.append((spy, qty))

        async with asyncio.timeout(90):  # a marketable limit fills in seconds
            while not (lots := list(await _all(factory, Lot))) or lots[0].qty < qty:  # noqa: ASYNC110
                await asyncio.sleep(1)
        [lot] = lots
        assert lot.qty == qty and lot.avg_price is not None
        [order] = await _all(factory, Order)
        assert order.status == OrderStatus.FILLED and order.mode == "paper"
        assert order.broker_order_id is not None  # a real Alpaca order id

        report = await Reconciler(
            factory,
            broker=broker,
            consumer=consumer,
            flags=FlagService(factory, h.alerts),
            alerts=h.alerts,
            config=h.config,
            clock=h.clock,
        ).run()
        assert report.clean and report.skipped is None, report
        assert report.account is not None and report.account.equity > 0
    finally:
        if consumer_task is not None:
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)
        for instrument, qty in flatten:  # flatten even if an assertion failed
            held = sum(
                p.qty for p in await AlpacaBroker(api).positions() if p.instrument == instrument
            )
            if held > 0:
                await AlpacaBroker(api).submit(
                    OrderRequest(
                        client_order_id=f"cta-flatten-{uuid.uuid4()}",
                        instrument=instrument,
                        side=Side.SELL,
                        qty=min(held, qty),
                        order_type=OrderType.LIMIT,
                        tif=TimeInForce.DAY,
                        limit_price=D("1.00"),  # marketable: through the market
                    )
                )
        if pool.closed is False:
            await pool.close()
        await api.aclose()


async def _all(factory: Factory, model: type) -> list:  # type: ignore[type-arg]
    async with factory() as db:
        return list((await db.scalars(select(model))).all())
