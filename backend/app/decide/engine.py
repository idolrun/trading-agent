"""`decide()`: pure decision engine (TRD §6). Same inputs -> same cards, no I/O.

Models interpret, code decides: quantities, prices, legs and the card tier are computed here
from validated values and config only. Confidence can only lower the tier (C31).
"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.cards.models import CardAction, CardType
from app.decide import risk, rules
from app.decide.types import BreakevenOptions, DecisionInputs, ProposedAction, StopProposal
from app.interpret.models import LinkOutcome, MsgType
from app.interpret.signal import F, SignalFields
from app.ledger.positions import OPENING
from app.validate.normalize import NormalizedSignal, Price, premium_prices

# Feed only (OD-18: conditional, tp_hit_notice); corrections are handled by F038.
FEED_ONLY = frozenset(
    {
        MsgType.WATCH,
        MsgType.CONDITIONAL,
        MsgType.COMMENTARY_PNL,
        MsgType.CHATTER,
        MsgType.OUT_OF_SCOPE_STRATEGY,
        MsgType.TP_HIT_NOTICE,
        MsgType.CORRECTION,
        MsgType.UNPARSED,
    }
)
FOLLOW_UP_ACTION = {
    MsgType.ADD: CardAction.ADD,
    MsgType.TRIM: CardAction.TRIM,
    MsgType.FULL_EXIT: CardAction.EXIT,
    MsgType.STOCK_EXIT: CardAction.EXIT,
    MsgType.SOFT_EXIT: CardAction.EXIT,
    MsgType.STOP_SET: CardAction.STOP,
    MsgType.STOP_MOVE: CardAction.STOP,
    MsgType.STOP_CANCEL: CardAction.STOP,
    MsgType.TP_SET: CardAction.TP,
}
SIZED = frozenset({CardAction.ENTRY, CardAction.ADD})
TIER_CANNOT_BUY = "tier cannot buy one contract"


def decide(i: DecisionInputs) -> list[ProposedAction]:
    cards = _propose(i)
    for stage in (risk.apply_staleness, risk.apply_entry_gates, risk.apply_warnings):
        cards = stage(i, cards)
    return cards


def _propose(i: DecisionInputs) -> list[ProposedAction]:
    """Sizing, exits, stops/TPs and the card tier (F023)."""
    t = i.signal.msg_type
    v = i.validation
    if (
        t in FEED_ONLY
        or v.status == "not_actionable"
        or t not in (OPENING | FOLLOW_UP_ACTION.keys())
    ):
        return []
    norm = v.normalized
    linked = i.link is not None and i.link.outcome == LinkOutcome.LINKED
    if t in OPENING:
        action = CardAction.ADD if linked else CardAction.ENTRY
    elif t == MsgType.TRIM and norm is not None and norm.exit_fraction == "all":
        action = CardAction.EXIT
    else:
        action = FOLLOW_UP_ACTION[t]
    valid_until = _valid_until(i, action)

    if t not in OPENING and not linked:  # ambiguous / none target -> review (PRD §10)
        outcome = i.link.outcome if i.link else LinkOutcome.NONE
        return [
            ProposedAction(
                action=action,
                card_type=CardType.REVIEW,
                review_reasons=[f"link_{outcome}"],
                missing_fields=["chosen_position_id"],
                candidate_position_ids=list(i.link.candidate_position_ids) if i.link else [],
                valid_until=valid_until,
            )
        ]
    if action != CardAction.ENTRY and i.lot is None:
        return []  # 7.3 step 2 / OD-18: provider ledger + feed only

    reasons = list(v.reasons)
    if v.status == "review" and "underlying_missing" in v.reasons:
        reasons.append("missing:underlying_confirmation")  # AC-14: never approvable unconfirmed
    reasons += [
        f"low_field_confidence:{name}"
        for name, confidence in sorted(i.signal.field_confidence.items())
        if confidence < i.config.t_field
    ]
    card = ProposedAction(
        action=action,
        card_type=CardType.NORMAL,
        valid_until=valid_until,
        warnings=list(v.warnings),
        suggested_underlying=v.suggested_underlying,
    )
    if norm is not None and v.status == "ok":
        card, more = _body(i, norm, card)
        reasons += more
    elif norm is None:
        reasons.append("not_normalized")

    missing = [r.removeprefix("missing:") for r in reasons if r.startswith("missing:")]
    reasons = list(dict.fromkeys(r for r in reasons if not r.startswith("missing:")))
    if reasons or missing:
        card_type = CardType.REVIEW
    elif card.disabled_reason is not None:
        card_type = CardType.DISABLED
    elif _has_context_field(i.signal.fields):
        card_type = CardType.INFERRED
    else:
        card_type = CardType.NORMAL
    return [
        card.model_copy(
            update={"card_type": card_type, "review_reasons": reasons, "missing_fields": missing}
        )
    ]


def _body(
    i: DecisionInputs, norm: NormalizedSignal, card: ProposedAction
) -> tuple[ProposedAction, list[str]]:
    """Qty, price, stop and legs for a validated signal; returns (card, review reasons)."""
    reasons: list[str] = []
    option = rules.is_option(i)
    if option and any(p.scale is None for p in premium_prices(norm)):
        reasons.append("price_scale_undecided")  # caller must scale against the contract quote
    update: dict[str, object] = {}
    lot_qty = i.lot.qty if i.lot else None

    if card.action in SIZED:
        qty, limit, sizing_reasons, disabled = _size(i, norm, option)
        reasons += sizing_reasons
        price = _signal_price(norm)
        update |= {
            "qty": qty,
            "limit_price": limit,
            "signal_price": price.value if price else None,
            "disabled_reason": disabled,
        }
        if norm.stop_level is not None:  # bracket at entry (C50)
            update["stop"] = _stop(norm.stop_basis, norm.stop_level.value, norm, option)
        update["tp_legs"] = rules.tp_legs([p.value for p in norm.tp_levels], qty)
    elif card.action in (CardAction.TRIM, CardAction.EXIT):
        assert lot_qty is not None  # noqa: S101 — decide() returned [] without a lot
        labels: list[str] = []
        if i.signal.msg_type == MsgType.SOFT_EXIT:
            qty, labels = None, ["suggestion"]  # C52: provider still holding
        elif card.action == CardAction.EXIT:
            qty = lot_qty
        else:
            qty = rules.exit_qty(norm.exit_fraction, lot_qty)
            if qty == 0:
                qty, labels = None, ["lot_too_small"]  # C82: exit all or skip
        update |= {"qty": qty, "labels": labels}
    elif card.action == CardAction.STOP:
        if i.signal.msg_type == MsgType.STOP_CANCEL or norm.stop_removed:
            update["stop"] = StopProposal(cancel=True)
        elif norm.stop_level is not None:
            update["stop"] = _stop(norm.stop_basis, norm.stop_level.value, norm, option)
        elif norm.breakeven_stop:
            update["stop"] = _stop("premium", None, norm, option)
            update["breakeven_options"] = BreakevenOptions(
                user_avg=i.lot.avg_price if i.lot else None,
                provider_entry=i.position.provider_entry_price if i.position else None,
            )
        else:
            reasons.append("missing:stop_level")
        if norm.candle_close:
            update["labels"] = ["candle_close"]
    elif card.action == CardAction.TP:
        if not norm.tp_levels:
            reasons.append("missing:tp_levels")
        update["tp_legs"] = rules.tp_legs([p.value for p in norm.tp_levels], lot_qty)
    return card.model_copy(update=update), reasons


def _size(
    i: DecisionInputs, norm: NormalizedSignal, option: bool
) -> tuple[int | None, Decimal | None, list[str], str | None]:
    """(qty, limit, review reasons, disabled reason). Config gaps leave qty blank: F024
    disables the card as "config incomplete"."""
    price = _signal_price(norm)
    if price is None:
        return None, None, ["missing:entry_price"], None
    tiers = i.config.option_tiers if option else i.config.stock_tiers
    chase = i.config.entry_chase_pct
    if tiers is None or chase is None:
        return None, None, [], None
    tier = norm.size_tier or "default"
    if tier not in tiers:
        return None, None, [f"unknown_size_tier:{tier}"], None
    if i.tick is None or i.tick <= 0:
        return None, None, ["tick_unknown"], None  # OD-25: never assume a tick
    limit = rules.entry_limit(price.value, chase, i.tick)
    qty = rules.size(tiers[tier], limit, option=option)
    if qty == 0:
        return None, limit, [], TIER_CANNOT_BUY  # never rounded up (7.4)
    return qty, limit, [], None


def _signal_price(norm: NormalizedSignal) -> Price | None:
    return norm.entry_price or norm.entry_price_high or norm.entry_price_low  # top of range


def _stop(
    basis: str | None, level: Decimal | None, norm: NormalizedSignal, option: bool
) -> StopProposal:
    # An option's underlying-price stop is system-held (C49); a stock stop is on the stock's
    # own price and held at the broker (C61), same as a premium stop (DB: system <=> underlying).
    underlying = basis == "underlying" and option
    return StopProposal(
        level=level,
        basis="underlying" if underlying else "premium",
        held_by="system" if underlying else "broker",
        candle_close=norm.candle_close,
    )


def _valid_until(i: DecisionInputs, action: CardAction) -> datetime | None:
    seconds = (
        i.config.entry_validity_s if action in SIZED else i.config.exit_card_timeout_s
    )  # C22, measured from post time (C90)
    return None if seconds is None else i.posted_at + timedelta(seconds=seconds)


def _has_context_field(fields: SignalFields) -> bool:
    for name in SignalFields.model_fields:
        value = getattr(fields, name)
        values = value if isinstance(value, list) else [value]
        if any(isinstance(f, F) and f.source.kind == "context" for f in values):
            return True
    return False
