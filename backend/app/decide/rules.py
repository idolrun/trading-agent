"""Pure price and bracket arithmetic (TRD §6, C56, C83). No I/O, and no quantity: the user
enters it (C105)."""

from decimal import ROUND_FLOOR, Decimal

from app.decide.types import DecisionInputs, TpLeg
from app.interpret.models import MsgType


def floor_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Round a buy limit down onto the tick grid: never pay more than the chase allows."""
    return (price / tick).to_integral_value(ROUND_FLOOR) * tick


def entry_limit(price: Decimal, chase_pct: Decimal, tick: Decimal) -> Decimal:
    return floor_to_tick(price * (1 + chase_pct / 100), tick)


def tp_legs(levels: list[Decimal], qty: int | None) -> list[TpLeg]:
    """Legs for the qty the user entered: min(levels, qty), equal split, remainder from TP1
    (5/3 -> 2/2/1; 2/3 -> 1/1)."""
    if not qty or not levels:
        return []
    n = min(len(levels), qty)
    base, extra = divmod(qty, n)
    return [TpLeg(level=levels[i], qty=base + (1 if i < extra else 0)) for i in range(n)]


def is_option(i: DecisionInputs) -> bool:
    """The card's instrument: the linked position's, else the validated signal's."""
    if i.position is not None:
        return i.position.instrument == "option"
    norm = i.validation.normalized
    return norm is not None and norm.msg_type != MsgType.STOCK_ENTRY and norm.instrument == "option"
