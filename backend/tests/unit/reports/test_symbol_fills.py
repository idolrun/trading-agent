"""Fills for one underlying stay split by instrument."""

from datetime import UTC, datetime
from decimal import Decimal

from app.reports.service import _symbol_fills

NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def test_option_and_stock_fills_are_not_combined() -> None:
    rows = [
        ("GOOGL", "option", "buy", 1, 2, Decimal("3"), NOW),
        ("GOOGL", "stock", "buy", 1, 10, Decimal("1000"), NOW),
        ("GOOGL260102C00357500", "option", "sell", 1, 1, Decimal("2"), NOW),
    ]
    by = {(row.symbol, row.instrument): row for row in _symbol_fills(rows)}
    option, stock = by[("GOOGL", "option")], by[("GOOGL", "stock")]
    assert option.buy_qty == 2 and option.sell_qty == 1
    assert option.avg_sell_price == Decimal("2")
    assert stock.buy_qty == 10 and stock.sell_qty == 0 and stock.avg_sell_price is None
