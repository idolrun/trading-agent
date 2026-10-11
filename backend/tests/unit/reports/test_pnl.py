"""F083: reporting arithmetic against hand-computed numbers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.ledger.models import Instrument
from app.reports import pnl
from app.reports.pnl import ClosedTrade, Outcome


def test_option_realized_uses_the_contract_multiplier() -> None:
    # bought 4 @ 1.00, sold 3 @ 1.50: (1.50 - 1.00) * 3 * 100
    assert pnl.realized_amount(D("1.5"), D("1"), 3, pnl.multiplier(Instrument.OPTION)) == D("150")
    # 10 shares: (51 - 50) * 10 * 1
    assert pnl.realized_amount(D("51"), D("50"), 10, pnl.multiplier(Instrument.STOCK)) == D("10")


def test_unknown_average_is_unknown_not_zero() -> None:
    assert pnl.realized_amount(D("1.5"), None, 3, 100) is None
    assert pnl.total([D("10"), None]) is None
    assert pnl.total([]) == D("0")
    assert pnl.unrealized_amount(D("1"), None, 2, 100) is None


def test_unrealized_marks_the_remaining_quantity() -> None:
    assert pnl.unrealized_amount(D("1.00"), D("0.70"), 2, 100) == D("-60")


def test_weighted_average_fill_price() -> None:
    assert pnl.weighted_average([(1, D("1.00")), (3, D("2.00"))]) == D("1.75")
    assert pnl.weighted_average([]) is None


def test_outcome_open_is_never_final() -> None:
    assert pnl.outcome(False, D("999")) is Outcome.OPEN
    assert pnl.outcome(True, None) is Outcome.UNKNOWN
    assert pnl.outcome(True, D("0")) is Outcome.BREAKEVEN
    assert pnl.outcome(True, D("1")) is Outcome.PROFIT
    assert pnl.outcome(True, D("-1")) is Outcome.LOSS


def test_summary_of_mixed_closed_trades() -> None:
    hour = timedelta(hours=1)
    s = pnl.summarize(
        [
            ClosedTrade(D("300"), hour),
            ClosedTrade(D("100"), 3 * hour),
            ClosedTrade(D("-150"), hour),
            ClosedTrade(D("0"), None),
            ClosedTrade(None, None),  # closed, basis missing: counted, never in a rate
        ]
    )
    assert (s.wins, s.losses, s.breakeven, s.unknown) == (2, 1, 1, 1)
    assert s.gross_profit == D("400") and s.gross_loss == D("-150")
    assert s.realized == D("250")
    assert s.win_rate == D("0.5")  # 2 of the 4 decided trades
    assert (s.avg_win, s.avg_loss) == (D("200"), D("-150"))
    assert (s.largest_win, s.largest_loss) == (D("300"), D("-150"))
    assert s.profit_factor == D("400") / D("150")
    assert s.avg_hold == 5 * hour / 3


def test_summary_without_losses_has_no_profit_factor_and_empty_is_all_none() -> None:
    assert pnl.summarize([ClosedTrade(D("5"), None)]).profit_factor is None
    s = pnl.summarize([])
    assert s.win_rate is None and s.realized is None and s.gross_profit is None


def test_cumulative_is_chronological_and_unknown_propagates() -> None:
    t0 = datetime(2026, 9, 28, 15, tzinfo=UTC)
    pts = [(t0 + timedelta(hours=2), D("-30")), (t0, D("100")), (t0 + timedelta(hours=1), D("20"))]
    assert pnl.cumulative(pts) == [D("100"), D("120"), D("90")]
    assert pnl.cumulative(
        [(t0, D("1")), (t0 + timedelta(hours=1), None), (t0 + timedelta(hours=2), D("1"))]
    ) == [D("1"), None, None]


def test_rate_and_change() -> None:
    assert pnl.rate(1, 4) == D("0.25") and pnl.rate(0, 0) is None
    assert pnl.change(D("5"), D("2")) == D("3") and pnl.change(None, D("2")) is None
