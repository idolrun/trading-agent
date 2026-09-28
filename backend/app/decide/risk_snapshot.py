"""Builds the `RiskSnapshot` for the entry gates (FR-18): DB reads, then quotes. No decisions;
anything unknown stays None so the gate disables entries instead of assuming 0.

PDT day-trade counting is not built until OD-3 decides the account type.
"""

import logging
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.decide.types import OpenLotBrief, RiskSnapshot
from app.instruments import Instrument, OptionContract, Stock
from app.ledger import repo
from app.ledger.lots import OPTION_MULTIPLIER, cost_basis
from app.ledger.models import Instrument as Kind
from app.ledger.models import Lot
from app.marketdata.port import MarketData
from app.markettime import ET, to_et
from app.validate.normalize import mid_price

logger = logging.getLogger(__name__)


async def build_risk_snapshot(
    sessions: async_sessionmaker[AsyncSession], marketdata: MarketData, now: datetime
) -> RiskSnapshot:
    day_start = datetime.combine(to_et(now).date(), time(0), tzinfo=ET)  # ET trading day
    async with sessions() as db:  # reads only; closed before any quote call
        lots = await repo.open_lots(db)
        sells = await repo.sells_since(db, day_start)

    realized: Decimal | None = None
    if all(avg is not None for _, _, avg, _ in sells):
        realized = sum(
            ((price - (avg or 0)) * qty * _multiplier(kind) for kind, qty, avg, price in sells),
            Decimal(0),
        )

    unrealized: Decimal | None = Decimal(0)
    exposure: Decimal | None = Decimal(0)
    for lot in lots:
        basis = cost_basis(lot.instrument, lot.qty, lot.avg_price)
        exposure = None if exposure is None or basis is None else exposure + basis
        mark = await _mark(marketdata, lot)
        if unrealized is None or mark is None or lot.avg_price is None:
            unrealized = None
        else:
            unrealized += (mark - lot.avg_price) * lot.qty * _multiplier(lot.instrument)

    return RiskSnapshot(
        realized_today=realized,
        unrealized=unrealized,
        open_positions=len(lots),
        exposure=exposure,
        lots=tuple(OpenLotBrief(lot.underlying, lot.option_right) for lot in lots),
    )


async def _mark(marketdata: MarketData, lot: Lot) -> Decimal | None:
    try:
        return mid_price(await marketdata.quote(_contract(lot)))
    except Exception:
        logger.warning("no quote for open lot", extra={"lot_id": str(lot.lot_id)})
        return None


def _contract(lot: Lot) -> Instrument:
    if lot.instrument == Kind.STOCK:
        return Stock(symbol=lot.underlying)
    assert lot.expiry and lot.strike and lot.option_right  # noqa: S101 — DB contract_shape
    return OptionContract(
        underlying=lot.underlying, expiry=lot.expiry, strike=lot.strike, right=lot.option_right
    )


def _multiplier(kind: Kind) -> int:
    return OPTION_MULTIPLIER if kind == Kind.OPTION else 1
