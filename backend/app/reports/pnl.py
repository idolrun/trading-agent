"""Reporting arithmetic (F083). Pure Decimal math: no SQL, no clock.

Conventions (docs/implementation/F083-trading-reports.md):
- Cost basis is the lot's weighted average (ledger/lots.py), not FIFO. A sell leaves it unchanged.
- Realized P&L of one reduction = (fill price - average before) x qty sold x multiplier.
  An unknown average before makes the reduction, and any total containing it, `None`.
- Unrealized P&L = (mark - lot average) x qty held x multiplier; `None` without a mark or average.
- Costs (fees, commissions) are not captured anywhere, so nothing here is net of costs.
- Unknown stays `None`; it is never turned into 0.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.ledger.lots import OPTION_MULTIPLIER
from app.ledger.models import Instrument

ZERO = Decimal(0)
LOW_SAMPLE_TRADES = 5  # fewer closed trades than this: an instrument is "insufficient data"


class Outcome(StrEnum):
    PROFIT = "profit"
    LOSS = "loss"
    BREAKEVEN = "breakeven"
    OPEN = "open"  # not final: part of the position is still held
    UNKNOWN = "unknown"  # closed, but a cost basis is missing


def multiplier(instrument: Instrument | str) -> int:
    return OPTION_MULTIPLIER if instrument == Instrument.OPTION else 1


def realized_amount(
    price: Decimal, avg_before: Decimal | None, qty: int, mult: int
) -> Decimal | None:
    if avg_before is None:
        return None
    return (price - avg_before) * qty * mult


def unrealized_amount(
    avg: Decimal | None, mark: Decimal | None, qty: int, mult: int
) -> Decimal | None:
    if avg is None or mark is None:
        return None
    return (mark - avg) * qty * mult


def total(values: Iterable[Decimal | None]) -> Decimal | None:
    """Sum, or None when any part is unknown (a partial total would be a wrong number)."""
    out = ZERO
    for v in values:
        if v is None:
            return None
        out += v
    return out


def weighted_average(fills: Iterable[tuple[int, Decimal]]) -> Decimal | None:
    """Quantity-weighted price of (qty, price) fills; None when nothing filled."""
    qty = 0
    value = ZERO
    for q, p in fills:
        qty += q
        value += q * p
    return value / qty if qty else None


def outcome(closed: bool, realized: Decimal | None) -> Outcome:
    if not closed:
        return Outcome.OPEN
    if realized is None:
        return Outcome.UNKNOWN
    if realized > 0:
        return Outcome.PROFIT
    return Outcome.LOSS if realized < 0 else Outcome.BREAKEVEN


@dataclass(frozen=True)
class ClosedTrade:
    realized: Decimal | None
    hold: timedelta | None


@dataclass(frozen=True)
class Summary:
    wins: int
    losses: int
    breakeven: int
    unknown: int
    gross_profit: Decimal | None  # None = no closed trade with a known result
    gross_loss: Decimal | None  # negative number, or None
    realized: Decimal | None
    win_rate: Decimal | None  # wins / (wins + losses + breakeven), 0..1
    avg_win: Decimal | None
    avg_loss: Decimal | None
    largest_win: Decimal | None
    largest_loss: Decimal | None
    profit_factor: Decimal | None  # gross profit / |gross loss|; None without a loss
    avg_hold: timedelta | None

    @property
    def decided(self) -> int:
        return self.wins + self.losses + self.breakeven


def summarize(trades: Sequence[ClosedTrade]) -> Summary:
    """Closed trades only. Trades with an unknown result are counted but never enter a rate."""
    wins = [t.realized for t in trades if t.realized is not None and t.realized > 0]
    losses = [t.realized for t in trades if t.realized is not None and t.realized < 0]
    known = [t.realized for t in trades if t.realized is not None]
    breakeven = len(known) - len(wins) - len(losses)
    gross_profit = sum(wins, ZERO) if known else None
    gross_loss = sum(losses, ZERO) if known else None
    holds = [t.hold for t in trades if t.hold is not None]
    return Summary(
        wins=len(wins),
        losses=len(losses),
        breakeven=breakeven,
        unknown=len(trades) - len(known),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        realized=sum(known, ZERO) if known else None,
        win_rate=Decimal(len(wins)) / len(known) if known else None,
        avg_win=sum(wins, ZERO) / len(wins) if wins else None,
        avg_loss=sum(losses, ZERO) / len(losses) if losses else None,
        largest_win=max(wins) if wins else None,
        largest_loss=min(losses) if losses else None,
        profit_factor=(
            gross_profit / -gross_loss if gross_profit is not None and gross_loss else None
        ),
        avg_hold=sum(holds, timedelta()) / len(holds) if holds else None,
    )


def cumulative(points: Sequence[tuple[datetime, Decimal | None]]) -> list[Decimal | None]:
    """Running total in time order; once a point is unknown the rest is unknown too."""
    out: list[Decimal | None] = []
    running: Decimal | None = ZERO
    for _, value in sorted(points, key=lambda p: p[0]):
        running = None if running is None or value is None else running + value
        out.append(running)
    return out


def change(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    """Absolute change versus the previous window; None when either side is unknown."""
    if current is None or previous is None:
        return None
    return current - previous


def rate(part: int, whole: int) -> Decimal | None:
    return Decimal(part) / whole if whole else None
