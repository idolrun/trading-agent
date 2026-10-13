"""Builds the `RiskSnapshot` for the entry gates (FR-18): DB reads, then quotes. No decisions;
anything unknown stays None so the gate disables entries instead of assuming 0.

PDT day-trade counting is not built until OD-3 decides the account type.
"""

import asyncio
import logging
import uuid
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.broker.port import BrokerAccount, BrokerClient
from app.decide.types import OpenLotBrief, RiskSnapshot
from app.execution import repo as orders
from app.instruments import Instrument, OptionContract, Stock, broker_symbol
from app.ledger import repo
from app.ledger.lots import OPTION_MULTIPLIER, cost_basis
from app.ledger.models import Instrument as Kind
from app.ledger.models import Lot
from app.marketdata.freshness import require_fresh
from app.marketdata.port import MarketData
from app.markettime import ET, to_et
from app.validate.normalize import mid_price

logger = logging.getLogger(__name__)


async def build_risk_snapshot(
    sessions: async_sessionmaker[AsyncSession],
    marketdata: MarketData,
    now: datetime,
    *,
    broker: BrokerClient | None = None,
    max_quote_age_s: int | None = None,
    exclude_card_id: uuid.UUID | None = None,
) -> RiskSnapshot:
    """`broker` given: the account (equity, cash, buying power) and the broker's positions are
    read fresh and `account_required` is set. Exposure and the position count then include the
    unfilled entry orders (a card approved but not yet filled still uses the limit), and a
    symbol the broker holds more of than the lots show counts at the broker's cost."""
    day_start = datetime.combine(to_et(now).date(), time(0), tzinfo=ET)  # ET trading day
    async with sessions() as db:  # reads only; closed before any quote call
        lots = await repo.open_lots(db)
        sells = await repo.sells_since(db, day_start)
        working = await orders.working_entries(db, exclude_card_id=exclude_card_id)

    realized: Decimal | None = None
    if all(avg is not None for _, _, avg, _ in sells):
        realized = sum(
            ((price - (avg or 0)) * qty * _multiplier(kind) for kind, qty, avg, price in sells),
            Decimal(0),
        )

    # Independent reads, so in parallel (F086): one quote per lot plus the broker account.
    marks, broker_read = await asyncio.gather(
        asyncio.gather(*(mark_lot(marketdata, lot, now, max_quote_age_s) for lot in lots)),
        _broker_state(broker) if broker is not None else _no_broker(),
    )
    unrealized: Decimal | None = Decimal(0)
    basis_by_symbol: dict[str, Decimal | None] = {}
    for lot, mark in zip(lots, marks, strict=True):
        basis = cost_basis(lot.instrument, lot.qty, lot.avg_price)
        held = basis_by_symbol.get(lot.broker_symbol, Decimal(0))
        basis_by_symbol[lot.broker_symbol] = None if held is None or basis is None else held + basis
        if unrealized is None or mark is None or lot.avg_price is None:
            unrealized = None
        else:
            unrealized += (mark - lot.avg_price) * lot.qty * _multiplier(lot.instrument)

    extra_positions = 0
    account: BrokerAccount | None
    account, broker_basis = broker_read
    for symbol, at_broker in broker_basis.items():
        if symbol not in basis_by_symbol:
            basis_by_symbol[symbol] = at_broker  # a holding no lot shows (adopted next run)
            extra_positions += 1
        elif (ledger := basis_by_symbol[symbol]) is not None and at_broker is not None:
            basis_by_symbol[symbol] = max(ledger, at_broker)

    working_exposure: Decimal | None = Decimal(0)
    new_symbols: set[str] = set()
    for symbol, kind, left, limit in working:
        working_exposure = (
            None
            if working_exposure is None or limit is None
            else working_exposure + left * limit * _multiplier(kind)
        )
        if symbol not in basis_by_symbol:
            new_symbols.add(symbol)

    exposure: Decimal | None = working_exposure
    for basis in basis_by_symbol.values():
        exposure = None if exposure is None or basis is None else exposure + basis

    return RiskSnapshot(
        realized_today=realized,
        unrealized=unrealized,
        open_positions=len(lots) + extra_positions + len(new_symbols),
        exposure=exposure,
        lots=tuple(OpenLotBrief(lot.underlying, lot.option_right) for lot in lots),
        account_required=broker is not None,
        equity=account.equity if account else None,
        cash=account.cash if account else None,
        buying_power=account.buying_power if account else None,
        options_buying_power=account.options_buying_power if account else None,
        working_exposure=working_exposure or Decimal(0),
        working_entries=len(new_symbols),
    )


async def _no_broker() -> tuple[None, dict[str, Decimal | None]]:
    return None, {}


async def _broker_state(
    broker: BrokerClient,
) -> tuple[BrokerAccount | None, dict[str, Decimal | None]]:
    """(account, cost per broker symbol). A failed read is None / empty: the gate that needs it
    then disables entries (never assumed fine)."""
    got_account, got_positions = await asyncio.gather(
        broker.account(), broker.positions(), return_exceptions=True
    )
    account: BrokerAccount | None = None
    if isinstance(got_account, BaseException):
        logger.error("broker account unavailable", exc_info=got_account)
    else:
        account = got_account
    basis: dict[str, Decimal | None] = {}
    try:
        if isinstance(got_positions, BaseException):
            raise got_positions
        for position in got_positions:
            kind = Kind.OPTION if isinstance(position.instrument, OptionContract) else Kind.STOCK
            symbol = broker_symbol(position.instrument)
            basis[symbol] = (
                None
                if position.avg_price is None
                else cost_basis(kind, position.qty, position.avg_price)
            )
    except Exception:
        logger.exception("broker positions unavailable")
        account = None  # cannot vouch for the portfolio: treat the whole read as failed
    return account, basis


async def mark_lot(
    marketdata: MarketData, lot: Lot, now: datetime, max_age_s: int | None
) -> Decimal | None:
    try:
        quote = require_fresh(await marketdata.quote(lot_contract(lot)), now, max_age_s)
        return mid_price(quote)
    except Exception:
        logger.warning("no usable quote for open lot", extra={"lot_id": str(lot.lot_id)})
        return None


def lot_contract(lot: Lot) -> Instrument:
    """What the user actually holds (a provider correction never changes it, 7.5)."""
    if lot.instrument == Kind.STOCK:
        return Stock(symbol=lot.underlying)
    assert lot.expiry and lot.strike and lot.option_right  # noqa: S101 — DB contract_shape
    return OptionContract(
        underlying=lot.underlying, expiry=lot.expiry, strike=lot.strike, right=lot.option_right
    )


def _multiplier(kind: Kind) -> int:
    return OPTION_MULTIPLIER if kind == Kind.OPTION else 1
