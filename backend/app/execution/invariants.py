"""FR-20 safety invariants (AC-3), pure: never open a short; never sell more than that
provider's lot; working sells never over-commit the lot. The broker read is the last word
(PRD §11: the broker is the source of truth)."""

import uuid
from dataclasses import dataclass

from app.execution.intent import OrderIntent
from app.execution.models import OrderKind, Side
from app.instruments import broker_symbol

PROTECTIVE = frozenset({OrderKind.STOP, OrderKind.TP})


@dataclass(frozen=True)
class LotView:
    lot_id: uuid.UUID
    provider_position_id: uuid.UUID | None
    broker_symbol: str
    qty: int
    is_open: bool


@dataclass(frozen=True)
class WorkingSells:
    """Unfilled qty of this lot's working sell orders."""

    exits: int = 0
    protective: int = 0  # stops + take-profits


def violations(
    intent: OrderIntent, lot: LotView | None, working: WorkingSells, broker_qty: int | None
) -> list[str]:
    if intent.side == Side.BUY:
        return [] if intent.kind == OrderKind.ENTRY else ["buy_only_for_entries"]
    if intent.kind == OrderKind.ENTRY:
        return ["entry_must_buy"]  # (1) no short
    if lot is None or not lot.is_open:
        return ["no_open_lot"]
    found = []
    if lot.broker_symbol != broker_symbol(intent.instrument):
        found.append("instrument_mismatch")
    if (
        intent.provider_position_id is not None
        and lot.provider_position_id != intent.provider_position_id
    ):
        found.append("other_providers_lot")  # (2) that provider's lot only (C24)
    if intent.kind in PROTECTIVE:
        if working.protective + intent.qty > lot.qty:
            found.append("protective_exceeds_lot")
    elif working.exits + intent.qty > lot.qty:
        found.append("sell_exceeds_lot")
    if broker_qty is None or broker_qty < intent.qty:
        found.append("broker_position_short")  # fresh broker read disagrees
    return found
