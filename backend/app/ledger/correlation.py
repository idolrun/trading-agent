"""Signal -> provider position correlation (FR-3, 7.3 step 1, TRD §8.1, §4.4, F069): pure.

Entries match by contract in the lane's book (C62). Follow-ups take the resolver's
deterministic pick (reply chain, thread, stated contract) when it has one; otherwise the
interpreter's hint is accepted only if it is above `t_link`, one of the resolver's candidates
AND consistent with every contract field the message states. Model confidence never overrides
the lane, the candidates or a contract mismatch. Anything else is an explicit AMBIGUOUS / NONE
that becomes a review card. Nothing upgrades ambiguity, not even a single open position
(PRD §10).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.interpret.models import LinkMethod, LinkOutcome, MsgType
from app.interpret.signal import LinkHint
from app.ledger.positions import OPENING, PositionState
from app.ledger.resolver import Resolution
from app.validate.normalize import NormalizedSignal

FOLLOW_UPS = frozenset(
    {
        MsgType.ADD,
        MsgType.TRIM,
        MsgType.FULL_EXIT,
        MsgType.SOFT_EXIT,
        MsgType.STOP_SET,
        MsgType.STOP_MOVE,
        MsgType.STOP_CANCEL,
        MsgType.TP_SET,
        MsgType.TP_HIT_NOTICE,
        MsgType.STOCK_EXIT,
        MsgType.CORRECTION,
    }
)
_ONE = Decimal(1)


@dataclass(frozen=True)
class LinkInputs:
    provider_id: int
    book: Mapping[uuid.UUID, PositionState]  # the lane's open/trimmed positions, in order
    resolution: Resolution | None = None  # None: no evidence (= every book position a candidate)


@dataclass(frozen=True)
class LinkDecision:
    """`position_id` is None for `new_position` until apply (F026) creates the row."""

    outcome: LinkOutcome
    method: LinkMethod
    confidence: Decimal | None
    position_id: uuid.UUID | None = None
    candidate_position_ids: tuple[uuid.UUID, ...] = ()
    evidence: dict[str, Any] | None = field(default=None, compare=False)  # message_links.evidence


def resolve_link(
    signal: NormalizedSignal,
    hint: LinkHint,
    ctx: LinkInputs,
    *,
    t_link: Decimal,
    hint_method: LinkMethod,
) -> LinkDecision | None:
    """None = the signal type never links (watch, commentary, chatter, ...)."""
    if signal.msg_type in OPENING:
        for position_id, position in ctx.book.items():
            if _same_contract(position, signal):  # C62: an entry on an open contract is an add
                return LinkDecision(
                    LinkOutcome.LINKED, LinkMethod.CODE, _ONE, position_id, evidence={"rule": "c62"}
                )
        return LinkDecision(LinkOutcome.NEW_POSITION, LinkMethod.CODE, _ONE)
    if signal.msg_type not in FOLLOW_UPS:
        return None
    if not ctx.book:
        return LinkDecision(
            LinkOutcome.NONE, hint_method, hint.confidence, evidence={"rule": "empty_book"}
        )

    r = ctx.resolution or Resolution.unresolved(ctx.book)
    candidates = tuple(p for p in r.candidates if p in ctx.book) or tuple(ctx.book)
    base: dict[str, Any] = {"resolver": r.evidence, "candidates": [str(p) for p in candidates]}

    picked = r.position_id
    if r.outcome == "unique" and picked is not None and picked in ctx.book and r.method:
        conflict = inconsistency(ctx.book[picked], signal)
        if conflict is None or r.method == LinkMethod.USER_CORRECTION:  # a person overrules it
            evidence = base | {"rule": r.method} | ({"conflict": conflict} if conflict else {})
            return LinkDecision(LinkOutcome.LINKED, r.method, _ONE, picked, evidence=evidence)
        return _ambiguous(hint_method, hint, candidates, base | {"conflict": conflict})

    target = hint.target
    if not isinstance(target, uuid.UUID):
        return _ambiguous(hint_method, hint, candidates, base | {"hint": str(target)})
    if target not in candidates:
        why = "not in the lane's book" if target not in ctx.book else "not a resolver candidate"
        return _ambiguous(hint_method, hint, candidates, base | {"hint_rejected": why})
    if hint.confidence is None or hint.confidence < t_link:
        return _ambiguous(hint_method, hint, candidates, base | {"hint_rejected": "below t_link"})
    if (conflict := inconsistency(ctx.book[target], signal)) is not None:
        return _ambiguous(hint_method, hint, candidates, base | {"hint_rejected": conflict})
    return LinkDecision(
        LinkOutcome.LINKED, hint_method, hint.confidence, target, evidence=base | {"rule": "hint"}
    )


def _ambiguous(
    method: LinkMethod, hint: LinkHint, candidates: tuple[uuid.UUID, ...], evidence: dict[str, Any]
) -> LinkDecision:
    return LinkDecision(
        LinkOutcome.AMBIGUOUS,
        method,
        hint.confidence,
        candidate_position_ids=candidates,
        evidence=evidence,
    )


def inconsistency(position: PositionState, signal: NormalizedSignal) -> str | None:
    """A contract field the message states that the position contradicts (F069 cross-check);
    unstated fields do not contradict."""
    instrument = "stock" if signal.msg_type == MsgType.STOCK_EXIT else signal.instrument
    if instrument in ("option", "stock") and instrument != position.instrument:
        return f"instrument {instrument} vs position {position.instrument}"
    stated = (
        ("underlying", signal.underlying, position.underlying),
        ("expiry", signal.expiry, position.expiry),
        ("strike", signal.strike, position.strike),
        ("right", signal.right, position.option_right),
    )
    for name, said, held in stated:
        if said is not None and said != held:
            return f"{name} {said} vs position {held}"
    return None


def _same_contract(position: PositionState, signal: NormalizedSignal) -> bool:
    instrument = "stock" if signal.msg_type == MsgType.STOCK_ENTRY else signal.instrument
    if instrument != position.instrument or signal.underlying != position.underlying:
        return False
    if instrument == "stock":
        return True
    return (signal.expiry, signal.strike, signal.right) == (
        position.expiry,
        position.strike,
        position.option_right,
    )
