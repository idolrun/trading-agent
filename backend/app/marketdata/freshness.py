"""Quote freshness (FR-12, OD-12): a quote older than `quote_max_age_s` is never used for an
actionable decision. Pure. `Quote.at` is the data source's own timestamp, so a delayed feed is
seen as delayed."""

from datetime import datetime, timedelta

from app.marketdata.port import Quote


class QuoteStale(Exception):
    """The quote is too old to act on (callers route to review / block, never guess)."""


def is_stale(quote: Quote, now: datetime, max_age_s: int | None) -> bool:
    """`max_age_s` None = not configured: entries are already blocked as config-incomplete, and
    nothing else is judged against an unset limit."""
    return max_age_s is not None and now - quote.at > timedelta(seconds=max_age_s)


def require_fresh(quote: Quote, now: datetime, max_age_s: int | None) -> Quote:
    if is_stale(quote, now, max_age_s):
        raise QuoteStale(f"quote is {(now - quote.at).total_seconds():.0f}s old (max {max_age_s}s)")
    return quote


def is_crossed(quote: Quote) -> bool:
    """Bid above ask: a broken quote, never a price to act on. A locked quote (bid == ask)
    happens in thin markets and is allowed."""
    return quote.bid is not None and quote.ask is not None and quote.bid > quote.ask
