"""Decision stages after sizing (F024): staleness, entry gates, warnings. Pure, no I/O.

FR-1c / C90: a stale entry/add yields no card (the ledger is still updated by apply); any
other stale or late card gets a `late` badge. FR-18 / C75: a blocked entry becomes a
disabled card with its reason (no push). OD-27: the kill switch disables entries/adds only;
exit and stop cards are still created so exits are never missed.
"""

from app.cards.models import CardAction, CardType
from app.decide.rules import is_option
from app.decide.types import DecisionInputs, FlagsView, ProposedAction, RiskSnapshot
from app.ledger.lots import OPTION_MULTIPLIER
from app.runtime_config.schema import RuntimeConfig, missing_for_entries

ENTRY_ACTIONS = frozenset({CardAction.ENTRY, CardAction.ADD})


def apply_staleness(i: DecisionInputs, cards: list[ProposedAction]) -> list[ProposedAction]:
    out = []
    for card in cards:
        if card.action in ENTRY_ACTIONS and i.stale:
            continue  # feed shows it as stale (inbox status), never an approvable card
        if i.stale or i.late:
            card = card.model_copy(update={"labels": [*card.labels, "late"]})
        out.append(card)
    return out


def entry_block_reason(
    config: RuntimeConfig,
    flags: FlagsView,
    risk: RiskSnapshot | None,
    card: ProposedAction,
    *,
    option: bool,
) -> str | None:
    """First failing gate for an entry/add card, else None. Also re-run at approval (F036)."""
    if missing := missing_for_entries(config):
        return "config incomplete: " + ", ".join(missing)
    if flags.kill_switch:
        return "kill switch active"
    if flags.entries_blocked:
        return "entries blocked: reconciliation mismatch"
    if risk is None or risk.unrealized is None:
        return "risk unknown: quote missing"
    if risk.realized_today is None or risk.exposure is None:
        return "risk unknown: cost basis missing"
    c = config
    max_loss, max_open, max_exposure = (
        c.max_daily_loss_usd,
        c.max_open_positions,
        c.max_total_exposure_usd,
    )
    assert max_loss and max_open and max_exposure  # noqa: S101 — config is complete here
    if -(risk.realized_today + risk.unrealized) >= max_loss:
        return "max daily loss reached"
    # an add grows an existing lot: it does not open a position
    if card.action == CardAction.ENTRY and risk.open_positions >= max_open:
        return "max open positions"
    if card.qty is not None and card.limit_price is not None:
        cost = card.qty * card.limit_price * (OPTION_MULTIPLIER if option else 1)
        if risk.exposure + cost > max_exposure:  # exposure includes unfilled working entries
            return "max exposure"  # no automatic down-sizing (not specified)
        if risk.account_required:
            available = (
                risk.options_buying_power
                if option and risk.options_buying_power is not None
                else risk.buying_power
            )
            if available is None:
                return "risk unknown: account unavailable"
            if cost > available:  # the broker already reserves working orders in buying power
                return "insufficient buying power"
    elif risk.account_required and risk.buying_power is None:
        return "risk unknown: account unavailable"
    return None


def apply_entry_gates(i: DecisionInputs, cards: list[ProposedAction]) -> list[ProposedAction]:
    out = []
    for card in cards:
        if card.action in ENTRY_ACTIONS and (
            reason := entry_block_reason(i.config, i.flags, i.risk, card, option=is_option(i))
        ):
            card = card.model_copy(
                update={"card_type": CardType.DISABLED, "disabled_reason": reason}
            )
        out.append(card)
    return out


def apply_warnings(i: DecisionInputs, cards: list[ProposedAction]) -> list[ProposedAction]:
    """C74: an open option lot in the opposite direction on the same underlying, any provider."""
    norm = i.validation.normalized
    right = i.position.option_right if i.position else (norm.right if norm else None)
    underlying = i.position.underlying if i.position else (norm.underlying if norm else None)
    if right is None or underlying is None or i.risk is None:
        return cards
    opposite = any(
        lot.underlying == underlying and lot.option_right not in (None, right)
        for lot in i.risk.lots
    )
    if not opposite:
        return cards
    warning = f"opposite_direction:{underlying}"
    return [
        card.model_copy(update={"warnings": [*card.warnings, warning]})
        if card.action in ENTRY_ACTIONS
        else card
        for card in cards
    ]
