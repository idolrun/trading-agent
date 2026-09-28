"""Pure sizing, exit and bracket arithmetic (TRD §6, C15, C51, C56, C76, C82, C83). No I/O."""

from decimal import ROUND_FLOOR, Decimal

from app.decide.types import DecisionInputs, TpLeg
from app.interpret.models import MsgType
from app.interpret.signal import ExitFraction
from app.ledger.lots import OPTION_MULTIPLIER

DIVISOR = {"half": 2, "third": 3, "quarter": 4}  # integer math: Decimal(1)/3 * 3 < 1


def floor_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Round a buy limit down onto the tick grid: never pay more than the chase allows."""
    return (price / tick).to_integral_value(ROUND_FLOOR) * tick


def entry_limit(price: Decimal, chase_pct: Decimal, tick: Decimal) -> Decimal:
    return floor_to_tick(price * (1 + chase_pct / 100), tick)


def size(tier_usd: Decimal, limit: Decimal, *, option: bool) -> int:
    """Whole contracts/shares the tier buys at `limit`; never rounded up (0 = cannot buy one)."""
    per_unit = limit * OPTION_MULTIPLIER if option else limit
    return int((tier_usd / per_unit).to_integral_value(ROUND_FLOOR))


def exit_qty(fraction: ExitFraction | None, lot_qty: int) -> int | None:
    """Pre-filled exit qty; None = blank, the user enters it (C26). May return 0 (C82)."""
    if fraction == "all":
        return lot_qty
    if fraction in DIVISOR:
        return lot_qty // DIVISOR[fraction]
    return None


def tp_legs(levels: list[Decimal], qty: int | None) -> list[TpLeg]:
    """min(levels, qty) legs, equal split, remainder from TP1 (5/3 -> 2/2/1; 2/3 -> 1/1)."""
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
