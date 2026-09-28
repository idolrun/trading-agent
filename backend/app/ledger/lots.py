"""User lot rules (FR-16, C24, C47, C62): pure, no SQL. Lots change only from fills and
reconciliation; the average is the system's own (may differ from broker FIFO, TRD §16.2 #10).
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from app.ledger.models import Instrument, LotChange, LotStatus

_FOUR_PLACES = Decimal("0.0001")  # avg_price numeric(12,4)
OPTION_MULTIPLIER = 100


@dataclass(frozen=True)
class LotUpdate:
    change: LotChange
    qty_before: int | None
    qty_after: int
    avg_before: Decimal | None
    avg_after: Decimal | None

    @property
    def status(self) -> LotStatus:
        return LotStatus.CLOSED if self.qty_after == 0 else LotStatus.OPEN


def apply_fill(
    held: tuple[int, Decimal | None] | None,
    side: Literal["buy", "sell"],
    qty: int,
    price: Decimal,
) -> LotUpdate:
    """`held` = (qty, avg) of the open lot, None when there is none (a buy opens one)."""
    if qty <= 0:
        raise ValueError("fill qty must be positive")
    if held is None:
        if side == "sell":
            raise ValueError("sell fill without an open lot")
        return LotUpdate(LotChange.OPEN, None, qty, None, price.quantize(_FOUR_PLACES))
    held_qty, avg = held
    if side == "buy":
        total = held_qty + qty
        new_avg = ((held_qty * (avg or price) + qty * price) / total).quantize(
            _FOUR_PLACES, ROUND_HALF_EVEN
        )
        return LotUpdate(LotChange.FILL_ADD, held_qty, total, avg, new_avg)
    # no-oversell is enforced before the order (F028); a fill beyond the lot is a bug
    if qty > held_qty:
        raise ValueError(f"sell fill {qty} exceeds lot qty {held_qty}")
    left = held_qty - qty
    return LotUpdate(
        LotChange.CLOSE if left == 0 else LotChange.FILL_REDUCE, held_qty, left, avg, avg
    )


def cost_basis(instrument: Instrument, qty: int, avg: Decimal | None) -> Decimal | None:
    """Dollars at cost; None when the average is unknown (never assume 0)."""
    if avg is None:
        return None
    return qty * avg * (OPTION_MULTIPLIER if instrument == Instrument.OPTION else 1)
