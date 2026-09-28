"""Validation (FR-11, FR-13, FR-14, C79, C84, C86): fetch market data, run the pure checks.

Never skips a check and never guesses: missing data -> review. Nothing here writes to the
ledger, and no DB transaction is open while market data is fetched.
"""

import logging
from collections.abc import Awaitable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.inbox.envelope import Envelope
from app.instruments import Instrument, Stock
from app.interpret.models import MsgType
from app.interpret.signal import ParsedSignal
from app.ledger import repo as ledger
from app.marketdata.cache import TtlCache
from app.marketdata.port import MarketData, Quote
from app.markettime import Clock
from app.runtime_config.schema import RuntimeConfig
from app.validate.identity import adjacent_strike_fits_better, find_in_chain
from app.validate.normalize import (
    CONTRACT_TYPES,
    NeedsReview,
    NormalizedSignal,
    identify,
    mid_price,
    scale_prices,
)
from app.validate.underlying_suggest import suggest

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    status: Literal["ok", "review", "not_actionable"]
    reasons: list[str] = []
    warnings: list[str] = []
    normalized: NormalizedSignal | None = None
    contract: Instrument | None = None
    quote: Quote | None = None
    suggested_underlying: str | None = None


class MarketDataUnavailable(Exception):
    pass


class ValidationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        marketdata: MarketData,
        alerts: AlertService,
        clock: Clock,
    ) -> None:
        self._sessions = session_factory
        self._md = marketdata
        self._alerts = alerts
        self._clock = clock
        self._optionable: TtlCache[str, frozenset[str]] = TtlCache(timedelta(hours=24), clock)
        self._expiries: TtlCache[str, list[date]] = TtlCache(timedelta(hours=1), clock)

    async def validate(
        self,
        signal: ParsedSignal,
        env: Envelope,
        *,
        config: RuntimeConfig,
        now: datetime,
        aliases: dict[str, str] | None = None,
    ) -> ValidationResult:
        norm = identify(signal, now=now, config=config, aliases=aliases)
        if isinstance(norm, NeedsReview):
            return _review(norm)
        needs_contract = (
            norm.instrument == "option" and norm.msg_type in CONTRACT_TYPES
        ) or norm.msg_type == MsgType.STOCK_ENTRY
        if not needs_contract:
            # Exits/stops use the linked position's contract (F021/F023).
            return ValidationResult(status="ok", normalized=norm)
        try:
            if norm.msg_type == MsgType.STOCK_ENTRY or norm.instrument == "stock":
                return await self._stock(norm)
            return await self._option(norm, env, config, now)
        except MarketDataUnavailable as exc:
            await self._market_data_alert(str(exc), now)
            return ValidationResult(
                status="review", reasons=["market_data_unavailable"], normalized=norm
            )

    async def _option(
        self, norm: NormalizedSignal, env: Envelope, config: RuntimeConfig, now: datetime
    ) -> ValidationResult:
        assert norm.strike is not None and norm.right is not None  # noqa: S101 — identify()
        if norm.underlying is None:  # FR-14 / C79: always a review card
            suggested = await self._suggest(env.provider_id, norm.strike, config, now)
            return ValidationResult(
                status="review",
                reasons=["underlying_missing"],
                normalized=norm,
                suggested_underlying=suggested,
            )
        underlying = norm.underlying
        if underlying not in await self._fetch(
            self._optionable.get("all", self._md.optionable_symbols)
        ):
            return ValidationResult(status="review", reasons=["not_optionable"], normalized=norm)

        async def load_expiries() -> list[date]:
            return await self._md.expiries(underlying)

        if norm.expiry not in await self._fetch(self._expiries.get(underlying, load_expiries)):
            return ValidationResult(
                status="review", reasons=["contract_not_in_chain"], normalized=norm
            )
        assert norm.expiry is not None  # noqa: S101 — identify()
        chain = await self._fetch(self._md.chain(underlying, norm.expiry))
        found = find_in_chain(chain, norm.strike, norm.right)
        if found is None:
            return ValidationResult(
                status="review", reasons=["contract_not_in_chain"], normalized=norm
            )
        contract, quote = found
        scaled = scale_prices(
            norm, mid=mid_price(quote), tolerance_pct=config.identity_price_tolerance_pct
        )
        if isinstance(scaled, NeedsReview):
            return _review(scaled, normalized=norm, contract=contract, quote=quote)
        warnings = []
        price = scaled.entry_price or scaled.entry_price_low or scaled.entry_price_high
        if (
            price is not None
            and config.adjacent_strike_ratio is not None
            and adjacent_strike_fits_better(
                chain, contract, price.value, config.adjacent_strike_ratio
            )
        ):
            warnings.append("adjacent_strike_better_match")  # warning only (FR-13, C78)
        return ValidationResult(
            status="ok", warnings=warnings, normalized=scaled, contract=contract, quote=quote
        )

    async def _stock(self, norm: NormalizedSignal) -> ValidationResult:
        if norm.underlying is None:
            return ValidationResult(
                status="review", reasons=["underlying_missing"], normalized=norm
            )
        if norm.entry_price is None:  # C60: no-price stock entry is not actionable
            return ValidationResult(
                status="not_actionable", reasons=["no_entry_price"], normalized=norm
            )
        stock = Stock(symbol=norm.underlying)
        quote = await self._fetch(self._md.quote(stock))
        scaled = scale_prices(norm, mid=mid_price(quote), tolerance_pct=None)
        assert isinstance(scaled, NormalizedSignal)  # noqa: S101 — stocks are never scaled
        return ValidationResult(status="ok", normalized=scaled, contract=stock, quote=quote)

    async def _suggest(
        self, provider_id: int, strike: Decimal, config: RuntimeConfig, now: datetime
    ) -> str | None:
        if config.missing_underlying_lookback_days is None:
            return None
        since = now - timedelta(days=config.missing_underlying_lookback_days)
        async with self._sessions() as db:
            recent = await ledger.recent_underlyings(db, provider_id, since)
        prices: dict[str, Decimal] = {}
        for symbol in recent:
            try:
                price = mid_price(await self._md.underlying_price(symbol))
            except Exception:
                logger.warning("no price for suggestion candidate", extra={"symbol": symbol})
                return None  # an unpriced candidate could be the match: suggest nothing
            if price is None:
                return None
            prices[symbol] = price
        return suggest(strike, prices, config.missing_underlying_proximity_pct)

    async def _fetch[T](self, awaitable: Awaitable[T]) -> T:
        try:
            return await awaitable
        except Exception as exc:
            raise MarketDataUnavailable(f"{type(exc).__name__}: {exc}") from exc

    async def _market_data_alert(self, error: str, now: datetime) -> None:
        try:
            await self._alerts.raise_(
                AlertKind.MARKET_DATA,
                Severity.WARNING,
                "Market data unavailable: signals go to review until it recovers",
                details={"error": error[:300]},
                dedupe_key=f"market_data:{now:%Y%m%d%H}",
            )
        except Exception:
            logger.exception("could not record market data alert")


def _review(
    needs: NeedsReview,
    *,
    normalized: NormalizedSignal | None = None,
    contract: Instrument | None = None,
    quote: Quote | None = None,
) -> ValidationResult:
    return ValidationResult(
        status="review",
        reasons=[*needs.reasons, *(f"missing:{m}" for m in needs.missing)],
        normalized=normalized,
        contract=contract,
        quote=quote,
    )
