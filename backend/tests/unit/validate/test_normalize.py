"""F018: symbols, expiries and price scale by code only (FR-12, FR-15, FR-15a, AC-17)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.instruments import OptionContract
from app.interpret.signal import F, LiteralSpan, ParsedSignal, SignalFields
from app.marketdata.port import Quote
from app.markettime import ET
from app.runtime_config.schema import RuntimeConfig
from app.validate.normalize import (
    NeedsReview,
    NormalizedSignal,
    identify,
    normalize,
    normalize_symbol,
    scale_prices,
)

MON = datetime(2026, 9, 28, 10, 4, tzinfo=ET)
FRI = datetime(2026, 10, 2, 11, 0, tzinfo=ET)
CFG = RuntimeConfig(identity_price_tolerance_pct=Decimal(20))


def lit(value: Any, raw: str | None = None) -> F[Any]:
    return F(value=value, source=LiteralSpan(raw=raw or str(value)))


def entry(price: str | None = "1.67", **fields: Any) -> ParsedSignal:
    base: dict[str, Any] = {
        "underlying": lit("googl"),
        "expiry_token": lit("fri"),
        "strike": lit(Decimal("357.5")),
        "right": lit("call", "C"),
    }
    if price is not None:
        base["entry_price"] = lit(Decimal(price))
    return ParsedSignal(
        msg_type="entry", instrument="option", fields=SignalFields(**(base | fields))
    )


def quote(mid: str) -> Quote:
    m = Decimal(mid)
    return Quote(bid=m, ask=m, last=None, at=MON)


def ok(result: NormalizedSignal | NeedsReview) -> NormalizedSignal:
    assert isinstance(result, NormalizedSignal), result
    return result


@pytest.mark.parametrize(
    ("raw", "expected", "rule"),
    [
        ("spx", "SPXW", "symbol_map"),
        ("$tsla", "TSLA", None),
        ("GOOGL", "GOOGL", None),
        ("wxyz", "WXYZ", None),  # unknown stays; validity is F019
        ("fb", "META", "alias"),
    ],
)
def test_symbols(raw: str, expected: str, rule: str | None) -> None:
    assert normalize_symbol(raw, {"SPX": "SPXW"}, {"fb": "META"}) == (expected, rule)


def test_ac17_spx_resolves_to_spxw_contract() -> None:
    signal = entry(None, underlying=lit("spx"), strike=lit(Decimal(7775)), expiry_token=lit("0dte"))
    norm = ok(identify(signal, now=MON, config=CFG))
    assert norm.contract() == OptionContract(
        underlying="SPXW", expiry=date(2026, 9, 28), strike=Decimal(7775), right="call"
    )
    assert (norm.underlying_raw, norm.symbol_rule) == ("spx", "symbol_map")


def test_expiry_resolution_and_review() -> None:
    assert ok(identify(entry(expiry_token=lit("0dte")), now=MON, config=CFG)).expiry == date(
        2026, 9, 28
    )
    assert ok(identify(entry(expiry_token=lit("friday")), now=FRI, config=CFG)).expiry == date(
        2026, 10, 2
    )
    bad = identify(entry(expiry_token=lit("soon")), now=MON, config=CFG)
    assert bad == NeedsReview(reasons=["expiry_unresolved"])


def test_missing_expiry_on_option_entry_is_never_assumed() -> None:
    signal = entry()
    signal.fields.expiry_token = None
    assert identify(signal, now=MON, config=CFG) == NeedsReview(
        reasons=["contract_incomplete"], missing=["expiry"]
    )


def test_exits_do_not_need_a_contract() -> None:
    trim = ParsedSignal(msg_type="trim", fields=SignalFields(entry_price=lit(Decimal("2.10"))))
    norm = ok(normalize(trim, now=MON, config=CFG, quote=None))
    assert norm.entry_price is not None and norm.entry_price.scale is None  # undecided


@pytest.mark.parametrize(
    ("raw", "mid", "expected", "scale"),
    [
        ("260", "2.55", "2.60", "per_contract"),
        ("260", "255", "260", "as_is"),  # stock-like option premium
        ("3.2", "3.15", "3.2", "as_is"),
        (".95", "0.90", "0.95", "as_is"),
    ],
)
def test_price_scale_matches_live_quote(raw: str, mid: str, expected: str, scale: str) -> None:
    norm = ok(normalize(entry(raw), now=MON, config=CFG, quote=quote(mid)))
    assert norm.entry_price is not None
    assert (norm.entry_price.value, norm.entry_price.scale) == (Decimal(expected), scale)
    assert norm.entry_price.raw == Decimal(raw)


def test_per_contract_hint() -> None:
    signal = entry("140", price_unit_hint="per_contract")
    norm = ok(normalize(signal, now=MON, config=CFG, quote=quote("5.00")))
    assert norm.entry_price is not None and norm.entry_price.value == Decimal("1.40")


def test_scale_applies_to_all_premium_levels() -> None:
    signal = entry(
        "260", stop_level=lit(Decimal(180)), tp_levels=[lit(Decimal(350)), lit(Decimal(500))]
    )
    norm = ok(normalize(signal, now=MON, config=CFG, quote=quote("2.55")))
    assert norm.stop_level is not None and norm.stop_level.value == Decimal("1.80")
    assert [t.value for t in norm.tp_levels] == [Decimal("3.5"), Decimal(5)]


def test_underlying_stop_is_never_scaled() -> None:
    signal = entry("260", stop_level=lit(Decimal(5790)), stop_basis="underlying")
    norm = ok(normalize(signal, now=MON, config=CFG, quote=quote("2.55")))
    assert norm.stop_level is not None and norm.stop_level.value == Decimal(5790)


@pytest.mark.parametrize(
    ("raw", "mid", "reason"),
    [
        ("5", "1.00", "price_off_quote"),  # 5x off quote
        ("100", "10", "price_scale_ambiguous"),  # 100 vs 1.00: exactly 10x either way
    ],
)
def test_implausible_prices_go_to_review(raw: str, mid: str, reason: str) -> None:
    result = normalize(entry(raw), now=MON, config=CFG, quote=quote(mid))
    assert result == NeedsReview(reasons=[reason])


def test_no_quote_or_no_tolerance_is_review() -> None:
    assert normalize(entry(), now=MON, config=CFG, quote=None) == NeedsReview(
        reasons=["market_data_unavailable"]
    )
    assert normalize(entry(), now=MON, config=RuntimeConfig(), quote=quote("1.6")) == NeedsReview(
        reasons=["config_incomplete:identity_price_tolerance_pct"]
    )


def test_stocks_are_not_scaled() -> None:
    stock = ParsedSignal(
        msg_type="stock_entry",
        instrument="stock",
        fields=SignalFields(underlying=lit("tsla"), entry_price=lit(Decimal(260))),
    )
    norm = ok(normalize(stock, now=MON, config=CFG, quote=quote("2.55")))
    assert norm.entry_price is not None and norm.entry_price.value == Decimal(260)


def test_exit_levels_matched_to_quote_without_entry() -> None:
    stop = ParsedSignal(
        msg_type="stop_set", instrument="option", fields=SignalFields(stop_level=lit(Decimal(150)))
    )
    norm = ok(normalize(stop, now=MON, config=CFG, quote=quote("1.80")))
    assert norm.stop_level is not None and norm.stop_level.value == Decimal("1.50")


@given(
    st.decimals(min_value=Decimal("0.01"), max_value=Decimal(10000), places=2),
    st.decimals(min_value=Decimal("0.01"), max_value=Decimal(10000), places=2),
)
def test_digits_never_change(raw: Decimal, mid: Decimal) -> None:
    norm = scale_prices(
        ok(identify(entry(str(raw)), now=MON, config=CFG)),
        mid=mid,
        tolerance_pct=Decimal(100),
    )
    if isinstance(norm, NormalizedSignal):
        assert norm.entry_price is not None
        assert norm.entry_price.value in (raw, raw / 100)
