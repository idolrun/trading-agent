from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.marketdata.freshness import QuoteStale, is_stale, require_fresh
from app.marketdata.port import Quote

NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def quote(age_s: int) -> Quote:
    return Quote(Decimal("1"), Decimal("1.1"), None, NOW - timedelta(seconds=age_s))


def test_stale_only_beyond_the_limit() -> None:
    assert not is_stale(quote(60), NOW, 60)  # exactly at the limit still counts as fresh
    assert is_stale(quote(61), NOW, 60)


def test_no_limit_judges_nothing() -> None:
    assert not is_stale(quote(10**6), NOW, None)


def test_require_fresh_raises_with_the_age() -> None:
    assert require_fresh(quote(5), NOW, 60).at == NOW - timedelta(seconds=5)
    with pytest.raises(QuoteStale, match="900s old"):
        require_fresh(quote(900), NOW, 60)
