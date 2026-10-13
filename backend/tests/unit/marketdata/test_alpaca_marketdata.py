from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.alpaca_api import AlpacaApi
from app.instruments import OptionContract, Stock
from app.marketdata.alpaca import AlpacaMarketData
from app.marketdata.port import MarketData, QuoteUnavailable
from app.markettime import FixedClock

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
OCC = "GOOGL261002C00357500"
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)


def md(routes: dict[str, Any]) -> tuple[AlpacaMarketData, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get(request.url.path, {})
        if callable(answer):
            answer = answer(request)
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    api = AlpacaApi("key", "secret", transport=httpx.MockTransport(handler))
    return AlpacaMarketData(api, FixedClock(NOW), poll_interval_s=0), seen


def snap(bid: Any = 1.6, ask: Any = 1.7, last: Any = 1.65, t: str = "2026-10-01T14:45:00Z") -> dict:
    return {"latestQuote": {"bp": bid, "ap": ask, "t": t}, "latestTrade": {"p": last, "t": t}}


def test_satisfies_port() -> None:
    port: MarketData = md({})[0]
    assert port


async def test_option_quote_uses_the_options_feed_and_alpacas_timestamp() -> None:
    data, seen = md({"/v1beta1/options/snapshots": {"snapshots": {OCC: snap()}}})
    quote = await data.quote(GOOGL)
    assert (quote.bid, quote.ask, quote.last) == (Decimal("1.6"), Decimal("1.7"), Decimal("1.65"))
    assert quote.at == datetime(2026, 10, 1, 14, 45, tzinfo=UTC)
    params = seen[0].url.params
    assert (params["symbols"], params["feed"]) == (OCC, "indicative")
    assert seen[0].headers["APCA-API-SECRET-KEY"] == "secret"


async def test_stock_quote_and_zero_sides_mean_no_quote() -> None:
    data, seen = md({"/v2/stocks/snapshots": {"SPY": snap(bid=0, ask=0, last=450.1)}})
    quote = await data.quote(Stock(symbol="SPY"))
    assert (quote.bid, quote.ask, quote.last) == (None, None, Decimal("450.1"))
    assert seen[0].url.params["feed"] == "iex"
    assert (await data.underlying_price("SPY")).last == Decimal("450.1")


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"SPY": snap(bid=0, ask=0, last=None)},
        httpx.Response(400, json={"message": "invalid symbol"}),
    ],
)
async def test_no_usable_data_is_unavailable(answer: Any) -> None:
    data, _ = md({"/v2/stocks/snapshots": answer})
    with pytest.raises(QuoteUnavailable):
        await data.underlying_price("SPY")


async def test_index_symbols_are_unavailable() -> None:
    data, _ = md(
        {"/v2/stocks/snapshots": httpx.Response(400, json={"message": "invalid symbol: SPX"})}
    )
    with pytest.raises(QuoteUnavailable):
        await data.underlying_price("SPX")


async def test_expiries_are_paged_deduplicated_and_sorted() -> None:
    pages = iter(
        [
            {
                "option_contracts": [
                    {"expiration_date": "2026-10-09"},
                    {"expiration_date": "2026-10-02"},
                ],
                "next_page_token": "p2",
            },
            {"option_contracts": [{"expiration_date": "2026-10-02"}], "next_page_token": None},
        ]
    )
    data, seen = md({"/v2/options/contracts": lambda _: next(pages)})
    assert await data.expiries("GOOGL") == [date(2026, 10, 2), date(2026, 10, 9)]
    assert seen[0].url.params["underlying_symbols"] == "GOOGL"
    assert seen[0].url.params["expiration_date_gte"] == "2026-10-01"
    assert seen[1].url.params["page_token"] == "p2"


async def test_index_root_expiries_query_the_index_and_keep_only_that_root() -> None:
    contracts = [
        {"expiration_date": "2026-10-16", "root_symbol": "SPX"},
        {"expiration_date": "2026-10-13", "root_symbol": "SPXW"},
    ]
    data, seen = md({"/v2/options/contracts": {"option_contracts": contracts}})
    assert await data.expiries("SPXW") == [date(2026, 10, 13)]
    assert seen[0].url.params["underlying_symbols"] == "SPX"


async def test_chain_keeps_only_the_requested_root_across_pages() -> None:
    pages = iter(
        [
            {"snapshots": {OCC: snap()}, "next_page_token": "p2"},
            {"snapshots": {"GOOGL1261002P00360000": snap(), "GOOGL261002P00360000": snap(2, 2.2)}},
        ]
    )
    data, seen = md({"/v1beta1/options/snapshots/GOOGL": lambda _: next(pages)})
    chain = await data.chain("GOOGL", date(2026, 10, 2))
    assert [(c.strike, c.right, q.bid) for c, q in chain] == [
        (Decimal("357.5"), "call", Decimal("1.6")),
        (Decimal("360"), "put", Decimal("2")),
    ]
    assert seen[0].url.params["expiration_date"] == "2026-10-02"


async def test_optionable_symbols() -> None:
    data, seen = md({"/v2/assets": [{"symbol": "SPY"}, {"symbol": "AAPL"}]})
    assert await data.optionable_symbols() == {"SPY", "AAPL", "SPX", "SPXW"}  # index roots
    assert seen[0].url.params["attributes"] == "options_enabled"


async def test_tick_sizes() -> None:
    data, _ = md({})
    assert await data.tick_size(Stock(symbol="AAPL"), Decimal(150)) == Decimal("0.01")
    assert await data.tick_size(GOOGL, Decimal("2.95")) == Decimal("0.05")
    assert await data.tick_size(GOOGL, Decimal("3.00")) == Decimal("0.10")


async def test_stream_polls_every_symbol_every_time() -> None:
    data, _ = md({"/v2/stocks/snapshots": {"SPY": snap(last=450), "QQQ": snap(last=380)}})
    stream = data.stream(["SPY", "QQQ", "NOPE"])
    got = [await anext(stream) for _ in range(4)]  # two polls: a quiet market is not a dead feed
    await stream.aclose()
    assert [(s, q.last) for s, q in got] == [
        ("SPY", Decimal(450)),
        ("QQQ", Decimal(380)),
        ("SPY", Decimal(450)),
        ("QQQ", Decimal(380)),
    ]


async def test_expiries_start_from_the_session_date_not_the_utc_date() -> None:
    late = datetime(2026, 10, 2, 1, 30, tzinfo=UTC)  # 21:30 ET on Oct 1
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"option_contracts": []})

    api = AlpacaApi("key", "secret", transport=httpx.MockTransport(handler))
    await AlpacaMarketData(api, FixedClock(late)).expiries("SPY")
    assert seen[0].url.params["expiration_date_gte"] == "2026-10-01"


async def test_one_refused_symbol_does_not_blind_the_others() -> None:
    def snapshots(request: httpx.Request) -> httpx.Response:
        if "SPX" in request.url.params["symbols"].split(","):
            return httpx.Response(400, json={"message": "invalid symbol: SPX"})
        return httpx.Response(200, json={"SPY": snap(last=450)})

    data, _ = md({"/v2/stocks/snapshots": snapshots})
    stream = data.stream(["SPX", "SPY"])
    symbol, quote = await anext(stream)
    await stream.aclose()
    assert (symbol, quote.last) == ("SPY", Decimal(450))


async def test_a_quote_without_any_timestamp_is_never_fresh() -> None:
    body = {"SPY": {"latestQuote": {"bp": 450, "ap": 450.1}, "latestTrade": {"p": 450.05}}}
    data, _ = md({"/v2/stocks/snapshots": body})
    quote = await data.quote(Stock(symbol="SPY"))
    assert quote.at == datetime(1970, 1, 1, tzinfo=UTC)
