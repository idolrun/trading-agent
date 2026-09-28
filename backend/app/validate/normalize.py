"""Normalization by code only (FR-12, FR-15, FR-15a, C78): broker symbols, resolved expiries
and the price scale that matches the live quote. Digits are never changed: every normalized
price is either the stated value or the stated value / 100.

Two phases so no I/O happens here: `identify()` (symbols, expiry, contract fields), then the
caller fetches the quote, then `scale_prices()`. `normalize()` composes both for callers that
already hold the quote.
"""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.instruments import Instrument, OptionContract, Stock
from app.interpret.models import MsgType
from app.interpret.signal import ExitFraction, F, ParsedSignal
from app.marketdata.port import Quote
from app.markettime import resolve_expiry_token
from app.runtime_config.schema import RuntimeConfig

HUNDRED = Decimal(100)
CONTRACT_TYPES = frozenset({MsgType.ENTRY, MsgType.ADD})  # need a full option contract
Scale = Literal["as_is", "per_contract"]


class NeedsReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    reasons: list[str] = []
    missing: list[str] = []


class Price(BaseModel):
    """A stated price and what it means: `value` = `raw` (as_is) or `raw / 100` (per_contract).
    `scale=None` until `scale_prices()` has decided (e.g. an exit before its contract is known).
    """

    model_config = ConfigDict(frozen=True)

    raw: Decimal
    value: Decimal
    scale: Scale | None = None

    def scaled(self, scale: Scale) -> "Price":
        return Price(
            raw=self.raw,
            value=self.raw / HUNDRED if scale == "per_contract" else self.raw,
            scale=scale,
        )


class NormalizedSignal(BaseModel):
    """Broker-ready values; raw text kept for the card ("140/con -> $1.40")."""

    model_config = ConfigDict(frozen=True)

    msg_type: MsgType
    instrument: Literal["option", "stock", "unclear"] | None
    underlying: str | None = None
    underlying_raw: str | None = None
    symbol_rule: Literal["symbol_map", "alias"] | None = None
    expiry: date | None = None
    expiry_raw: str | None = None
    strike: Decimal | None = None
    right: Literal["call", "put"] | None = None
    entry_price: Price | None = None
    entry_price_low: Price | None = None
    entry_price_high: Price | None = None
    stop_level: Price | None = None
    stop_basis: Literal["premium", "underlying"] | None = None
    tp_levels: tuple[Price, ...] = ()
    price_unit_hint: Literal["per_share", "per_contract"] | None = None
    size_tier: str | None = None
    qty_stated: int | None = None
    exit_fraction: ExitFraction | None = None
    candle_close: bool = False
    breakeven_stop: bool = False
    stop_removed: bool = False
    is_add: bool = False
    prose_condition: str | None = None

    def contract(self) -> Instrument | None:
        """The named instrument when fully specified."""
        if self.instrument == "stock" and self.underlying:
            return Stock(symbol=self.underlying)
        if (
            self.instrument == "option"
            and self.underlying
            and self.expiry
            and self.strike
            and self.right
        ):
            return OptionContract(
                underlying=self.underlying, expiry=self.expiry, strike=self.strike, right=self.right
            )
        return None


def normalize_symbol(
    raw: str, symbol_map: dict[str, str], aliases: dict[str, str]
) -> tuple[str, Literal["symbol_map", "alias"] | None]:
    """Uppercase, strip `$`, then config symbol map (SPX -> SPXW), then approved aliases."""
    symbol = raw.strip().lstrip("$").upper()
    rule: Literal["symbol_map", "alias"] | None = None
    mapped = {k.upper(): v.upper() for k, v in symbol_map.items()}
    if symbol in mapped:
        symbol, rule = mapped[symbol], "symbol_map"
    upper_aliases = {k.upper(): v.upper() for k, v in aliases.items()}
    if symbol in upper_aliases:
        symbol, rule = upper_aliases[symbol], "alias"
    return symbol, rule


def identify(
    signal: ParsedSignal,
    *,
    now: datetime,
    config: RuntimeConfig,
    aliases: dict[str, str] | None = None,
) -> NormalizedSignal | NeedsReview:
    f = signal.fields
    underlying = rule = None
    if f.underlying is not None:
        underlying, rule = normalize_symbol(f.underlying.value, config.symbol_map, aliases or {})
    expiry = None
    if f.expiry_token is not None:
        expiry = resolve_expiry_token(f.expiry_token.value, now)
        if expiry is None:
            return NeedsReview(reasons=["expiry_unresolved"])
    if signal.instrument == "option" and signal.msg_type in CONTRACT_TYPES:
        missing = [
            name
            for name, value in (("expiry", expiry), ("strike", f.strike), ("right", f.right))
            if value is None
        ]
        if missing:  # no default expiry/strike is ever assumed
            return NeedsReview(reasons=["contract_incomplete"], missing=missing)
    return NormalizedSignal(
        msg_type=signal.msg_type,
        instrument=signal.instrument,
        underlying=underlying,
        underlying_raw=f.underlying.value if f.underlying else None,
        symbol_rule=rule,
        expiry=expiry,
        expiry_raw=f.expiry_token.value if f.expiry_token else None,
        strike=_v(f.strike),
        right=_v(f.right),
        entry_price=_price(f.entry_price),
        entry_price_low=_price(f.entry_price_low),
        entry_price_high=_price(f.entry_price_high),
        stop_level=_price(f.stop_level),
        stop_basis=f.stop_basis,
        tp_levels=tuple(Price(raw=t.value, value=t.value) for t in f.tp_levels),
        price_unit_hint=f.price_unit_hint,
        size_tier=_v(f.size_tier),
        qty_stated=_v(f.qty_stated),
        exit_fraction=f.exit_fraction,
        candle_close=f.candle_close,
        breakeven_stop=f.breakeven_stop,
        stop_removed=f.stop_removed,
        is_add=f.is_add,
        prose_condition=f.prose_condition,
    )


def scale_prices(
    norm: NormalizedSignal, *, mid: Decimal | None, tolerance_pct: Decimal | None
) -> NormalizedSignal | NeedsReview:
    """Pick `as_is` or `per_contract` for the option premium prices (stocks: as_is).

    `/con` forces per_contract. Otherwise the entry price decides: the candidate closest to the
    live mid (by ratio), which must lie within `tolerance_pct`; every premium price in the
    signal then uses that scale. Without an entry price each level is matched to the mid on
    its own; without a mid it stays undecided (`scale=None`).
    """
    if norm.instrument == "stock" or norm.price_unit_hint == "per_share":
        return _with_scale(norm, lambda _p: "as_is")
    if norm.price_unit_hint == "per_contract":
        return _with_scale(norm, lambda _p: "per_contract")
    if norm.instrument != "option":
        return norm  # instrument not known yet (e.g. exit before linking): undecided
    anchor = norm.entry_price or norm.entry_price_low or norm.entry_price_high
    if anchor is not None:
        if mid is None or mid <= 0:
            return NeedsReview(reasons=["market_data_unavailable"])
        if tolerance_pct is None:
            return NeedsReview(reasons=["config_incomplete:identity_price_tolerance_pct"])
        scale = closest_scale(anchor.raw, mid)
        if scale is None:
            return NeedsReview(reasons=["price_scale_ambiguous"])
        chosen = anchor.scaled(scale).value
        if abs(chosen - mid) / mid * HUNDRED > tolerance_pct:
            return NeedsReview(reasons=["price_off_quote"])
        return _with_scale(norm, lambda _p: scale)
    if mid is None or mid <= 0:
        return norm
    scales: dict[Decimal, Scale] = {}
    for price in premium_prices(norm):
        if (each := closest_scale(price.raw, mid)) is None:
            return NeedsReview(reasons=["price_scale_ambiguous"])
        scales[price.raw] = each
    return _with_scale(norm, lambda p: scales[p.raw])


def normalize(
    signal: ParsedSignal,
    *,
    now: datetime,
    config: RuntimeConfig,
    quote: Quote | None,
    aliases: dict[str, str] | None = None,
) -> NormalizedSignal | NeedsReview:
    identified = identify(signal, now=now, config=config, aliases=aliases)
    if isinstance(identified, NeedsReview):
        return identified
    return scale_prices(
        identified, mid=mid_price(quote), tolerance_pct=config.identity_price_tolerance_pct
    )


def closest_scale(raw: Decimal, mid: Decimal) -> Scale | None:
    """The reading of `raw` whose ratio to `mid` is closest to 1 (None on an exact tie)."""
    if raw <= 0:
        return None
    as_is = _distance(raw / mid)
    per_contract = _distance(raw / HUNDRED / mid)
    if as_is == per_contract:
        return None
    return "as_is" if as_is < per_contract else "per_contract"


def _distance(ratio: Decimal) -> Decimal:
    """How far a ratio is from 1, symmetric in both directions (2x and 0.5x are equal)."""
    return max(ratio, 1 / ratio)


def mid_price(quote: Quote | None) -> Decimal | None:
    if quote is None:
        return None
    if quote.bid is not None and quote.ask is not None:
        return (quote.bid + quote.ask) / 2
    return quote.last


def premium_prices(norm: NormalizedSignal) -> list[Price]:
    prices = [norm.entry_price, norm.entry_price_low, norm.entry_price_high, *norm.tp_levels]
    if norm.stop_basis != "underlying":
        prices.append(norm.stop_level)
    return [p for p in prices if p is not None]


def _with_scale(norm: NormalizedSignal, pick: Callable[[Price], Scale]) -> NormalizedSignal:
    def fix(p: Price | None, premium: bool = True) -> Price | None:
        if p is None:
            return None
        return p.scaled(pick(p)) if premium else p.scaled("as_is")

    return norm.model_copy(
        update={
            "entry_price": fix(norm.entry_price),
            "entry_price_low": fix(norm.entry_price_low),
            "entry_price_high": fix(norm.entry_price_high),
            "stop_level": fix(norm.stop_level, premium=norm.stop_basis != "underlying"),
            "tp_levels": tuple(p.scaled(pick(p)) for p in norm.tp_levels),
        }
    )


def _v[T](field: F[T] | None) -> T | None:
    return None if field is None else field.value


def _price(field: F[Decimal] | None) -> Price | None:
    return None if field is None else Price(raw=field.value, value=field.value)
