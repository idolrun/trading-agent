"""FR-20 safety invariants (AC-3), pure: never open a short; never sell more than that
provider's lot; working sells never over-commit the lot: exits + protective exposure <= lot on
every sell (an exit and a stop never both hold the same contracts). The broker read is the
last word (PRD §11: the broker is the source of truth)."""

import uuid
from dataclasses import dataclass, field

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
    protective: int = 0  # stops + take-profits outside any OCO group
    # OD-30: one bracket's legs (stop vs TPs) cancel each other: group -> (stop qty, TP qty)
    groups: dict[uuid.UUID, tuple[int, int]] = field(default_factory=dict)


def protective_exposure(working: WorkingSells, intent: OrderIntent | None = None) -> int:
    """Contracts the protective orders can sell at most: ungrouped legs add up; within an OCO
    group the stop and the TP legs are alternatives, so the group counts max(stop, sum TP)."""
    ungrouped, groups = working.protective, dict(working.groups)
    if intent is not None:
        if intent.oco_group_id is None:
            ungrouped += intent.qty
        else:
            stop, tp = groups.get(intent.oco_group_id, (0, 0))
            groups[intent.oco_group_id] = (
                (stop + intent.qty, tp)
                if intent.kind == OrderKind.STOP
                else (stop, tp + intent.qty)
            )
    return ungrouped + sum(max(stop, tp) for stop, tp in groups.values())


def excluding(working: WorkingSells, intent: OrderIntent) -> WorkingSells:
    """`working` without the intent's own stored row (a retry re-checks an intent that is
    already counted as working)."""
    if intent.side != Side.SELL:
        return working
    if intent.kind not in PROTECTIVE:
        return WorkingSells(max(0, working.exits - intent.qty), working.protective, working.groups)
    if intent.oco_group_id is None:
        return WorkingSells(working.exits, max(0, working.protective - intent.qty), working.groups)
    groups = dict(working.groups)
    stop, tp = groups.get(intent.oco_group_id, (0, 0))
    groups[intent.oco_group_id] = (
        (max(0, stop - intent.qty), tp)
        if intent.kind == OrderKind.STOP
        else (stop, max(0, tp - intent.qty))
    )
    return WorkingSells(working.exits, working.protective, groups)


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
        protective = protective_exposure(working, intent)
        if protective > lot.qty:
            found.append("protective_exceeds_lot")
        elif working.exits + protective > lot.qty:
            found.append("sells_exceed_lot")  # (3) a working exit already holds those contracts
    elif working.exits + intent.qty > lot.qty:
        found.append("sell_exceeds_lot")
    elif working.exits + intent.qty + protective_exposure(working) > lot.qty:
        found.append("sells_exceed_lot")  # (3) F043 reduces protection before the exit
    if broker_qty is None or broker_qty < intent.qty:
        found.append("broker_position_short")  # fresh broker read disagrees
    return found
