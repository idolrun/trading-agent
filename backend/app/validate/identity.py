"""Contract identity checks over already-fetched chain data (FR-11, FR-13). Pure; never
modifies a field — a better-fitting adjacent strike is only a warning (C78)."""

from decimal import Decimal
from typing import Literal

from app.instruments import OptionContract
from app.marketdata.port import Quote
from app.validate.normalize import mid_price

Chain = list[tuple[OptionContract, Quote]]


def find_in_chain(
    chain: Chain, strike: Decimal, right: Literal["call", "put"]
) -> tuple[OptionContract, Quote] | None:
    for contract, quote in chain:
        if contract.strike == strike and contract.right == right:
            return contract, quote
    return None


def adjacent_strike_fits_better(
    chain: Chain, contract: OptionContract, price: Decimal, ratio: Decimal
) -> bool:
    """True if a neighbouring listed strike's mid is closer to `price` by at least `ratio`
    (adjacent distance <= ratio x the named contract's distance)."""
    same_right = sorted(
        ((c, q) for c, q in chain if c.right == contract.right), key=lambda cq: cq[0].strike
    )
    strikes = [c.strike for c, _ in same_right]
    if contract.strike not in strikes:
        return False
    i = strikes.index(contract.strike)
    named_mid = mid_price(same_right[i][1])
    if named_mid is None:
        return False
    named_distance = abs(named_mid - price)
    for j in (i - 1, i + 1):
        if 0 <= j < len(same_right) and (mid := mid_price(same_right[j][1])) is not None:
            if abs(mid - price) <= named_distance * ratio and abs(mid - price) < named_distance:
                return True
    return False
