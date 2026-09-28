"""C22 validity, OD-14 supersession and approval completeness. Pure, no SQL.

Validity is measured from the provider's post time (C90): `valid_until` was set by decide from
`posted_at`, never from card creation. Entry cards also die on the price band; exit, stop and
TP cards never die by price, only by timeout or a closed position.
"""

from datetime import datetime
from decimal import Decimal

from app.cards.models import CardAction, CardReason
from app.decide.types import ProposedAction
from app.interpret.models import MsgType
from app.marketdata.port import Quote

ENTRY_ACTIONS = frozenset({CardAction.ENTRY, CardAction.ADD})
# 7.2: a newer provider message on the same position (trim, stop, exit or correction)
SUPERSEDING = frozenset(
    {
        MsgType.TRIM,
        MsgType.FULL_EXIT,
        MsgType.STOCK_EXIT,
        MsgType.STOP_SET,
        MsgType.STOP_MOVE,
        MsgType.STOP_CANCEL,
        MsgType.CORRECTION,
    }
)


def check_entry(
    proposal: ProposedAction, quote: Quote | None, now: datetime, chase_pct: Decimal
) -> CardReason | None:
    """TIMEOUT past `valid_until`; PRICE_BAND when the live ask is above signal + X%.
    No quote means the band cannot be checked: the caller routes that to review (OD-12)."""
    if proposal.valid_until is not None and now > proposal.valid_until:
        return CardReason.TIMEOUT
    price = proposal.signal_price
    if quote is not None and quote.ask is not None and price is not None:
        if quote.ask > price * (1 + chase_pct / 100):
            return CardReason.PRICE_BAND
    return None


def check_exit(proposal: ProposedAction, position_closed: bool, now: datetime) -> CardReason | None:
    """Never by price (7.3 step 4)."""
    if proposal.valid_until is not None and now > proposal.valid_until:
        return CardReason.TIMEOUT
    if position_closed and proposal.action in (CardAction.TRIM, CardAction.EXIT):
        return CardReason.SUPERSEDED
    return None


def supersedes(msg_type: MsgType, card_action: CardAction) -> bool:
    """OD-14: a linked trim/stop/exit/correction invalidates pending entry/add cards on P."""
    return msg_type in SUPERSEDING and card_action in ENTRY_ACTIONS


def approval_blockers(
    proposal: ProposedAction, edits: dict[str, object], lot_qty: int | None
) -> list[str]:
    """What still prevents approval after the user's edits; [] = approvable.

    Review cards need every missing field (7.1 step 5); blank-qty cards need 1 <= qty <= lot
    for sells (C26, C52, C82, re-checked by submit_order); disabled cards never."""
    if proposal.disabled_reason is not None:
        return ["disabled"]
    blockers = [f for f in proposal.missing_fields if edits.get(f) in (None, "")]
    needs_qty = proposal.action in ENTRY_ACTIONS | {CardAction.TRIM, CardAction.EXIT}
    qty = edits.get("qty", proposal.qty)
    if needs_qty:
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            blockers.append("qty")
        elif proposal.action not in ENTRY_ACTIONS and (lot_qty is None or qty > lot_qty):
            blockers.append("qty_exceeds_lot")
    if (
        proposal.action == CardAction.STOP
        and proposal.stop is not None
        and not proposal.stop.cancel
        and proposal.stop.level is None
        and edits.get("stop_level") is None
    ):
        blockers.append("stop_level")  # breakeven: the user picks one of both prices (C29)
    return blockers
