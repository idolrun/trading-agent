"""F083: trading reports over a small book whose every number is worked out by hand.

The book (average-cost lots, option multiplier 100, all times UTC; NOW = Tue 2026-09-29 15:00,
11:00 ET):

  L1 GOOGL 357.5C 10/2, paper, from A's entry signal (card from the real pipeline)
       buy 2 @ 1.00, buy 2 @ 1.50          -> average 1.25
       sell 3 @ 2.00 (Mon 15:30)           -> (2.00 - 1.25) * 3 * 100 = +225
       sell 1 @ 1.00 (Mon 16:30)           -> (1.00 - 1.25) * 1 * 100 = -25   closed, +200
  L2 MSFT stock, paper, no signal (foreign): buy 10 @ 100, sell 10 @ 95 (today) -> -50, closed
  L3 AAPL stock, paper, open: buy 5 @ 50 (today); quote mid 60                  -> unrealized +50
  L4 NVDA stock, paper, 40 days ago: buy 10 @ 10, sell 10 @ 11                  -> +10, closed
  L5 TSLA stock, SHADOW (today): buy 100 @ 1, sell 100 @ 11                     -> +1000, closed
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.cards.models import Card
from app.db import in_transaction
from app.execution.models import Fill, Order
from app.instruments import Instrument, OptionContract, Stock, broker_symbol
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.ledger.models import Lot
from app.marketdata.fake import FakeMarketData
from tests.integration.api.conftest import Api
from tests.integration.pipeline.harness import Harness, opened_position, option_entry
from tests.messages import PROVIDER_A

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
MON = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
TODAY = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)
BASE = "/api/v1/reports/trading"
Leg = tuple[str, int, str, datetime]  # side, qty, price, when

ROUTES = [
    "overview",
    "signals",
    "orders",
    "pnl",
    "instruments",
    "trades",
    f"trades/{uuid.uuid4()}",
    "timeseries",
]


@pytest.mark.parametrize("route", ROUTES)
async def test_every_report_requires_a_session(api: Api, route: str) -> None:
    r = await api.client.get(f"{BASE}/{route}")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


async def trade(
    h: Harness,
    contract: Instrument,
    legs: list[Leg],
    *,
    mode: str = "paper",
    card_id: uuid.UUID | None = None,
    position_id: uuid.UUID | None = None,
    provider_id: int | None = None,
) -> uuid.UUID:
    """Record `legs` the way the fill intake would: order, fill, then the lot change."""
    lot: Lot | None = None
    async with in_transaction(h.factory, None) as db:
        for side, qty, price, at in legs:
            order = Order(
                client_order_id=f"cta-{uuid.uuid4()}",
                auto_reason="reprice",
                card_id=card_id if side == "buy" else None,
                lot_id=lot.lot_id if lot else None,
                kind="entry" if side == "buy" else "exit",
                side=side,
                instrument="option" if isinstance(contract, OptionContract) else "stock",
                broker_symbol=broker_symbol(contract),
                qty=qty,
                order_type="limit",
                limit_price=Decimal(price),
                tif="day",
                status="filled",
                mode=mode,
                filled_qty=qty,
                avg_fill_price=Decimal(price),
                created_at=at,
            )
            db.add(order)
            await db.flush()
            fill = Fill(
                order_id=order.order_id,
                broker_fill_id=f"f-{uuid.uuid4()}",
                qty=qty,
                price=Decimal(price),
                filled_at=at,
            )
            db.add(fill)
            await db.flush()
            held = (lot.qty, lot.avg_price) if lot and lot.status == "open" else None
            lot = await ledger.record_lot(
                db,
                apply_fill(held, side, qty, Decimal(price)),  # type: ignore[arg-type]
                row=lot,
                instrument=contract,
                provider_position_id=position_id,
                provider_id=provider_id,
                fill_id=fill.fill_id,
                at=at,
            )
            order.lot_id = lot.lot_id
    assert lot is not None
    return lot.lot_id


@pytest.fixture
async def book(signed_in: Api, tmp_path: Path) -> tuple[Api, dict[str, uuid.UUID]]:
    api = signed_in
    api.app.state.clock.set(NOW)
    h = await Harness.create(api.app.state.session_factory, tmp_path)
    a1 = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )
    await h.process(a1)
    async with h.factory() as db:
        card = (await db.scalars(select(Card).where(col(Card.action) == "entry"))).one()
    m = lambda **kw: MON + timedelta(**kw)  # noqa: E731
    lots = {
        "L1": await trade(
            h,
            GOOGL,
            [
                ("buy", 2, "1.00", m(minutes=1)),
                ("buy", 2, "1.50", m(minutes=2)),
                ("sell", 3, "2.00", m(hours=1)),
                ("sell", 1, "1.00", m(hours=2)),
            ],
            card_id=card.card_id,
            position_id=opened_position(a1),
            provider_id=PROVIDER_A,
        ),
        "L2": await trade(
            h,
            Stock(symbol="MSFT"),
            [("buy", 10, "100", TODAY), ("sell", 10, "95", TODAY + timedelta(minutes=30))],
        ),
        "L3": await trade(h, Stock(symbol="AAPL"), [("buy", 5, "50", TODAY)]),
        "L4": await trade(
            h,
            Stock(symbol="NVDA"),
            [
                ("buy", 10, "10", NOW - timedelta(days=40)),
                ("sell", 10, "11", NOW - timedelta(days=40, minutes=-5)),
            ],
        ),
        "L5": await trade(
            h,
            Stock(symbol="TSLA"),
            [("buy", 100, "1", TODAY), ("sell", 100, "11", TODAY + timedelta(minutes=20))],
            mode="shadow",
        ),
    }
    # never the developer's real market data adapter: a scripted book of one quote
    api.app.state.marketdata = FakeMarketData()
    api.app.state.marketdata.set_quote(Stock(symbol="AAPL"), Decimal("59"), Decimal("61"))
    return api, lots


def params(**kw: str) -> dict[str, str]:
    return {"mode": "paper", "period": "3d", **kw}


async def get(api: Api, route: str, **kw: str) -> dict:  # type: ignore[type-arg]
    r = await api.client.get(f"{BASE}/{route}", params=params(**kw))
    assert r.status_code == 200, r.text
    body: dict = r.json()  # type: ignore[type-arg]
    return body


async def test_overview_separates_realized_unrealized_and_signal_stages(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    o = await get(api, "overview")
    # realized: L1 +200 and L2 -50 (L4 is 40 days old, L5 is shadow); open L3 has no sells
    assert o["realizedPnl"]["current"] == "150"
    assert o["unrealizedPnl"] == "50"  # (60 - 50) * 5 shares, now
    assert o["netPnl"] == "200" and o["openPositions"] == 1
    assert (o["wins"], o["losses"], o["breakeven"]) == (1, 1, 0)
    assert o["winRate"] == "0.5" and o["profitFactor"] == "4"
    assert o["avgWin"] == "200" and o["avgLoss"] == "-50"
    assert o["largestWin"] == "200" and o["largestLoss"] == "-50"
    # signals: A's entry made an approvable card; nobody approved it, so it is not "approved"
    assert o["signals"]["current"] == 1 and o["acceptedSignals"]["current"] == 1
    assert o["rejectedSignals"]["current"] == 0 and o["approved"] == 0
    assert o["pendingCards"] == 1
    # executions are counted from fills, not from accepted signals
    assert o["executedTrades"]["current"] == 3  # L1, L2, L3 had fills in the window
    assert (o["buyFills"], o["sellFills"]) == (4, 3)
    assert o["fees"] is None and o["coverage"]["costsIncluded"] is False


async def test_period_filter_and_overall_are_real_windows(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    today = await get(api, "overview", period="1d")
    assert today["realizedPnl"]["current"] == "-50"  # only L2's sell is today (paper)
    assert today["coverage"]["window"]["label"] == "Today so far (ET)"
    # Overall is computed from the whole history, not a sum of the presets
    overall = await get(api, "overview", period="all")
    assert overall["realizedPnl"]["current"] == "160"  # +200 -50 +10
    assert overall["realizedPnl"]["previous"] is None  # no previous window for "all"
    assert overall["wins"] == 2
    # the 3 days before the window (Sep 24-26) hold no sells: a known zero, not "unavailable"
    three = await get(api, "overview")
    assert three["realizedPnl"]["previous"] == "0"


async def test_mode_filter_keeps_simulated_and_real_apart(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    shadow = await get(api, "overview", mode="shadow")
    assert shadow["realizedPnl"]["current"] == "1000"
    both = await get(api, "overview", mode="all")
    assert both["realizedPnl"]["current"] == "1150"


async def test_custom_range_boundaries_and_errors(book: tuple[Api, dict[str, uuid.UUID]]) -> None:
    api, _ = book
    # ET days Sep 28..Sep 28 include Monday's sells (+200) but not Tuesday's L2 loss
    mon = await get(api, "overview", period="custom", startDate="2026-09-28", endDate="2026-09-28")
    assert mon["realizedPnl"]["current"] == "200"
    for bad in (
        {"period": "custom"},
        {"period": "custom", "startDate": "2026-09-30", "endDate": "2026-09-01"},
        {"period": "7d", "startDate": "2026-09-01"},
    ):
        r = await api.client.get(f"{BASE}/overview", params={"mode": "paper", **bad})
        assert r.status_code == 422 and r.json()["code"] == "invalid", bad


async def test_trades_are_lots_with_hand_checked_figures(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, lots = book
    t = await get(api, "trades", sort="symbol", order="asc")
    rows = {r["underlying"]: r for r in t["rows"]}
    assert set(rows) == {"AAPL", "GOOGL", "MSFT"} and t["totalRows"] == 3
    g = rows["GOOGL"]
    assert g["lotId"] == str(lots["L1"]) and g["status"] == "closed" and g["outcome"] == "profit"
    assert (g["entryQty"], g["exitQty"], g["openQty"], g["requestedQty"]) == (4, 4, 0, 4)
    assert g["avgEntryPrice"] == "1.25" and g["realizedPnl"] == "200"
    assert Decimal(g["avgExitPrice"]) == Decimal("1.75")  # (3 * 2.00 + 1 * 1.00) / 4
    assert g["entryCost"] == "500"  # 4 contracts * 1.25 * 100
    assert g["attribution"] == "single" and g["signalId"] is not None
    assert g["holdSeconds"] == 7140  # 14:31 .. 16:30
    m = rows["MSFT"]
    assert m["outcome"] == "loss" and m["realizedPnl"] == "-50" and m["attribution"] == "none"
    a = rows["AAPL"]
    # an open trade is never given a final outcome from its unrealized P&L
    assert a["outcome"] == "open" and a["status"] == "open" and a["openQty"] == 5
    assert a["realizedPnl"] == "0" and a["unrealizedPnl"] == "50" and a["netPnl"] == "50"
    assert a["fees"] is None and g["fees"] is None


async def test_trades_filter_sort_and_paginate_on_the_server(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    losers = await get(api, "trades", outcome="loss")
    assert [r["underlying"] for r in losers["rows"]] == ["MSFT"]
    by_pnl = await get(api, "trades", state="closed", sort="realized", order="desc")
    assert [r["underlying"] for r in by_pnl["rows"]] == ["GOOGL", "MSFT"]
    page = await get(api, "trades", sort="symbol", order="asc", pageSize="2", page="2")
    assert page["totalRows"] == 3 and [r["underlying"] for r in page["rows"]] == ["MSFT"]
    found = await get(api, "trades", search="goo")
    assert [r["underlying"] for r in found["rows"]] == ["GOOGL"]
    by_symbol = await get(api, "trades", symbol="AAPL")
    assert [r["underlying"] for r in by_symbol["rows"]] == ["AAPL"]


async def test_trade_detail_shows_the_lifecycle_without_inventing_steps(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, lots = book
    d = await get(api, f"trades/{lots['L1']}")
    assert d["trade"]["realizedPnl"] == "200"
    assert [o["side"] for o in d["orders"]] == ["buy", "buy", "sell", "sell"]
    assert [(f["qty"], f["price"]) for f in d["fills"]] == [
        (2, "1"),
        (2, "1.5"),
        (3, "2"),
        (1, "1"),
    ]
    assert [s["change"] for s in d["lotHistory"]] == [
        "open",
        "fill_add",
        "fill_reduce",
        "close",
    ]
    assert d["lotHistory"][2]["avgBefore"] == "1.25"
    assert len(d["signals"]) == 1 and "Bought GOOGL" in d["signals"][0]["text"]
    assert d["decisions"][0]["decided"] is None  # never approved by a person: not invented
    assert d["audit"] == []
    assert d["outcomeExplanation"] == (
        "Closed. 4 sold at an average 1.75 against an average cost of 1.25: "
        "realized $200.00, before fees (not captured)."
    )
    # an open trade: explained as not final
    open_lot = await get(api, f"trades/{lots['L3']}")
    assert open_lot["outcomeExplanation"].startswith("Open, not final")
    missing = await api.client.get(f"{BASE}/trades/{uuid.uuid4()}", params=params())
    assert missing.status_code == 404


async def test_pnl_series_is_cumulative_and_keeps_empty_days_flagged(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    p = await get(api, "pnl")
    assert p["realized"] == "150" and p["grossProfit"] == "200" and p["grossLoss"] == "-50"
    assert p["netAfterCosts"] is None and p["fees"] is None
    series = {pt["bucket"]: pt for pt in p["series"]}
    assert list(series) == ["2026-09-27", "2026-09-28", "2026-09-29"]
    assert series["2026-09-27"]["hasActivity"] is False and series["2026-09-27"]["realized"] == "0"
    assert series["2026-09-28"]["cumulative"] == "200"
    assert series["2026-09-29"]["realized"] == "-50" and series["2026-09-29"]["cumulative"] == "150"
    assert Decimal(p["avgHoldSeconds"]) > 0


async def test_signal_outcomes_attribute_only_unshared_lots(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    s = await get(api, "signals")
    out = s["outcomes"]
    assert out["acceptedSignals"] == 1 and out["withOrders"] == 1 and out["withFills"] == 1
    # the book was seeded without an Approval row, so "approved" stays 0 while the fills exist:
    # the report states each stage from its own record and never infers one from another
    assert out["profitable"] == 1 and out["losing"] == 0 and out["realizedPnl"] == "200"
    assert out["winRate"] == "1" and out["attributionUnavailable"] == 0
    stages = {st["key"]: st["count"] for st in s["funnel"]}
    assert stages["received"] == 1 and stages["accepted"] == 1 and stages["approved"] == 0
    assert stages["ordered"] == 1 and stages["filled"] == 1 and stages["pnl"] == 1
    assert s["conversionToApproval"] == "0"
    assert s["rows"][0]["engineStatus"] == "accepted"
    assert s["rows"][0]["approvalStatus"] == "pending"


async def test_orders_report_separates_requested_from_filled(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    o = await get(api, "orders", period="all")
    assert o["submitted"] == 9 and o["filled"] == 9 and o["orderToFillRate"] == "1"
    assert o["buyQtyRequested"] == o["buyQtyFilled"]
    by = {r["symbol"]: r for r in o["bySymbol"]}
    assert by["GOOGL"]["instrument"] == "option"
    assert by["GOOGL"]["buyQty"] == 4 and by["GOOGL"]["avgBuyPrice"] == "1.25"
    sell = next(r for r in o["rows"] if r["side"] == "sell")
    assert sell["executedNotional"] is not None and sell["fees"] is None
    page = await get(api, "orders", period="all", side="sell", pageSize="2")
    assert page["totalRows"] == 4 and len(page["rows"]) == 2


async def test_instruments_rollup_and_low_sample_flag(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    i = await get(api, "instruments", sort="net_pnl", order="desc")
    rows = {r["symbol"]: r for r in i["rows"]}
    g = rows["GOOGL"]
    assert g["realizedPnl"] == "200" and g["netPnl"] == "200" and g["closedTrades"] == 1
    assert g["winRate"] == "1" and g["lowSample"] is True  # one trade proves nothing
    assert g["acceptedSignals"] == 1 and g["buyOrders"] == 2 and g["sellOrders"] == 2
    a = rows["AAPL"]
    assert a["openQty"] == 5 and a["unrealizedPnl"] == "50" and a["winRate"] is None
    assert next(r["symbol"] for r in i["rows"]) == "GOOGL"
    bad = await api.client.get(f"{BASE}/instruments", params=params(sort="nope"))
    assert bad.status_code == 422


async def test_missing_quote_makes_unrealized_unknown_not_zero(
    book: tuple[Api, dict[str, uuid.UUID]],
) -> None:
    api, _ = book
    del api.app.state.marketdata.quotes["AAPL"]
    o = await get(api, "overview")
    assert o["unrealizedPnl"] is None and o["netPnl"] is None
    assert o["coverage"]["unpricedLots"] == 1
    assert o["realizedPnl"]["current"] == "150"  # realized does not depend on a quote


async def test_timeseries_one_point_per_bucket(book: tuple[Api, dict[str, uuid.UUID]]) -> None:
    api, _ = book
    r = await api.client.get(
        f"{BASE}/timeseries",
        params={**params(), "metrics": "realized_pnl,wins,losses,signals"},
    )
    assert r.status_code == 200
    ts = r.json()
    assert ts["granularity"] == "day" and ts["metrics"] == [
        "realized_pnl",
        "wins",
        "losses",
        "signals",
    ]
    by = {p["bucket"]: p["values"] for p in ts["points"]}
    assert by["2026-09-28"]["wins"] == "1" and by["2026-09-28"]["signals"] == "1"
    assert by["2026-09-29"]["losses"] == "1" and by["2026-09-29"]["realized_pnl"] == "-50"
    bad = await api.client.get(f"{BASE}/timeseries", params={**params(), "metrics": "nope"})
    assert bad.status_code == 422


async def test_one_day_window_buckets_by_hour(book: tuple[Api, dict[str, uuid.UUID]]) -> None:
    api, _ = book
    ts = await get(api, "timeseries", period="1d")
    assert ts["granularity"] == "hour"
    assert ts["points"][0]["bucket"] == "2026-09-29T00:00"
    assert ts["points"][-1]["bucket"] == "2026-09-29T10:00"  # now is 11:00 ET sharp


async def test_empty_book_returns_zeros_and_unknowns_correctly(signed_in: Api) -> None:
    signed_in.app.state.clock.set(NOW)
    signed_in.app.state.marketdata = FakeMarketData()
    o = await get(signed_in, "overview", mode="all")
    assert o["signals"] == {"current": 0, "previous": 0}
    assert o["realizedPnl"]["current"] == "0"  # no sells is a known zero ...
    assert o["winRate"] is None and o["profitFactor"] is None  # ... but there is no rate
    assert o["unrealizedPnl"] == "0" and o["openPositions"] == 0
    t = await get(signed_in, "trades", mode="all")
    assert t["rows"] == [] and t["totalRows"] == 0


async def test_unknown_cost_basis_is_unknown_never_zero(signed_in: Api, tmp_path: Path) -> None:
    api = signed_in
    api.app.state.clock.set(NOW)
    api.app.state.marketdata = FakeMarketData()
    h = await Harness.create(api.app.state.session_factory, tmp_path)
    async with in_transaction(h.factory, None) as db:
        lot = await ledger.create_foreign_lot(db, Stock(symbol="IBM"), 10, None, reason="adopted")
        order = Order(
            client_order_id="cta-ibm",
            auto_reason="eod_forced_close",
            lot_id=lot.lot_id,
            kind="exit",
            side="sell",
            instrument="stock",
            broker_symbol="IBM",
            qty=10,
            order_type="limit",
            tif="day",
            status="filled",
            mode="paper",
            filled_qty=10,
            avg_fill_price=Decimal(5),
            created_at=TODAY,
        )
        db.add(order)
        await db.flush()
        fill = Fill(
            order_id=order.order_id,
            broker_fill_id="f-ibm",
            qty=10,
            price=Decimal(5),
            filled_at=TODAY,
        )
        db.add(fill)
        await db.flush()
        await ledger.record_lot(
            db,
            apply_fill((10, None), "sell", 10, Decimal(5)),
            row=lot,
            fill_id=fill.fill_id,
            at=TODAY,
        )
    o = await get(api, "overview")
    assert o["realizedPnl"]["current"] is None  # the sell has no cost basis: not 0, not a guess
    assert o["unknownOutcomes"] == 1 and o["winRate"] is None and o["wins"] == 0
    assert o["coverage"]["lotsWithoutBasis"] == 1
    t = await get(api, "trades")
    assert t["rows"][0]["outcome"] == "unknown" and t["rows"][0]["realizedPnl"] is None
    assert t["rows"][0]["netPnl"] is None


async def test_partial_and_unfilled_orders_keep_requested_and_filled_apart(
    signed_in: Api, tmp_path: Path
) -> None:
    api = signed_in
    api.app.state.clock.set(NOW)
    api.app.state.marketdata = FakeMarketData()
    h = await Harness.create(api.app.state.session_factory, tmp_path)
    async with in_transaction(h.factory, None) as db:
        for coid, status, filled, price in (
            ("cta-part", "partially_filled", 4, Decimal(2)),
            ("cta-wait", "working", 0, None),
            ("cta-rej", "rejected", 0, None),
        ):
            order = Order(
                client_order_id=coid,
                auto_reason="reprice",
                kind="entry",
                side="buy",
                instrument="stock",
                broker_symbol="AMD",
                qty=10,
                order_type="limit",
                limit_price=Decimal(2),
                tif="day",
                status=status,
                mode="paper",
                filled_qty=filled,
                avg_fill_price=price,
                created_at=TODAY,
            )
            db.add(order)
            await db.flush()
            if filled:
                db.add(
                    Fill(
                        order_id=order.order_id,
                        broker_fill_id=f"f-{coid}",
                        qty=filled,
                        price=Decimal(2),
                        filled_at=TODAY,
                    )
                )
    o = await get(api, "orders")
    assert (o["submitted"], o["partiallyFilled"], o["rejected"], o["pending"]) == (3, 1, 1, 2)
    assert o["buyQtyRequested"] == 30 and o["buyQtyFilled"] == 4
    assert o["executedNotional"] == "8"  # 4 shares * 2.00: only what actually filled
    rows = {r["status"]: r for r in o["rows"]}
    assert (rows["partially_filled"]["requestedQty"], rows["partially_filled"]["filledQty"]) == (
        10,
        4,
    )
    assert rows["partially_filled"]["avgFillPrice"] == "2"
    assert rows["working"]["avgFillPrice"] is None and rows["working"]["executedNotional"] is None
    assert o["brokerRejections"] == [{"source": "broker", "reason": "unspecified", "count": 1}]
