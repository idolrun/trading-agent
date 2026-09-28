"""Missing-underlying suggestion by live-price proximity (C84, TRD §6). Pure."""

from decimal import Decimal


def suggest(strike: Decimal, prices: dict[str, Decimal], proximity_pct: Decimal) -> str | None:
    """The one recently traded underlying whose price is within ±P% of the strike.
    Zero or several matches -> no suggestion (never guess between them)."""
    matches = [
        symbol
        for symbol, price in prices.items()
        if price > 0 and abs(strike - price) / price * 100 <= proximity_pct
    ]
    return matches[0] if len(matches) == 1 else None
