"""Exit price path (C25, OD-20). Pure: start at the mid, step toward the bid, never below the
floor, always on the tick grid."""

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal


def round_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(ROUND_HALF_UP) * tick


def floor_price(mid: Decimal, floor_pct: Decimal, tick: Decimal) -> Decimal:
    """OD-20 (confirmed 2026-09-29): `exit_floor_pct` below the mid at exit start, never
    below one tick; rounded up so the floor itself is never undercut."""
    floor = (mid * (1 - floor_pct / 100) / tick).to_integral_value(ROUND_CEILING) * tick
    return max(tick, floor)


def next_price(
    price: Decimal, mid: Decimal, bid: Decimal, step_pct: Decimal, floor: Decimal, tick: Decimal
) -> Decimal:
    """One re-price step: down by `step_pct` of the mid-bid spread, at least one tick, onto
    the grid, never below the floor. Monotone non-increasing."""
    step = max(tick, (mid - bid) * step_pct / 100)
    lower = ((price - step) / tick).to_integral_value(ROUND_FLOOR) * tick
    return max(floor, min(price, lower))
