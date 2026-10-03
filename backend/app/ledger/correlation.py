"""Signal -> provider position correlation (FR-3, 7.3 step 1, TRD §8.1, §4.4, F069): pure.

Entries match by contract in the lane's book (C62). Follow-ups take the resolver's
deterministic pick (reply chain, thread, stated contract, sole candidate) when it has one and
the message's stated contract does not contradict it. Otherwise the follow-up is AMBIGUOUS: a
review card. The interpreter's `LinkHint` is a proposal, never a resolution (F077): when it
names a candidate it is recorded as `evidence.proposal` (rule `model_proposed`, with why it
would have been refused: not a candidate, below `t_link`, or a contract conflict) so the card
can show it, but model confidence never turns ambiguity into a link.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.interpret.models import LinkMethod, LinkOutcome, MsgType
from app.interpret.signal import LinkHint
from app.ledger.positions import OPENING, SETUP_TYPES, PositionState
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
    setups: Mapping[uuid.UUID, PositionState] = field(default_factory=dict)  # today's, F072


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
        # F072: the entry a watch / conditional setup was waiting for (exactly one fits)
        fits = [p for p, s in ctx.setups.items() if _fits_setup(s, signal)]
        if len(fits) == 1:
            return LinkDecision(
                LinkOutcome.LINKED, LinkMethod.CODE, _ONE, fits[0], evidence={"rule": "setup"}
            )
        return LinkDecision(LinkOutcome.NEW_POSITION, LinkMethod.CODE, _ONE)
    if signal.msg_type in SETUP_TYPES:
        if signal.underlying is None:
            return None  # nothing to watch: feed only
        same = [p for p, s in ctx.setups.items() if s.underlying == signal.underlying]
        if len(same) == 1:  # a restatement ("still watching SPY", "SPY over 770 for calls")
            return LinkDecision(
                LinkOutcome.LINKED, LinkMethod.CODE, _ONE, same[0], evidence={"rule": "setup"}
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

    return _ambiguous(
        hint_method,
        hint,
        candidates,
        base | _proposal(hint, hint_method, ctx, candidates, signal, t_link),
    )


def _proposal(
    hint: LinkHint,
    method: LinkMethod,
    ctx: LinkInputs,
    candidates: tuple[uuid.UUID, ...],
    signal: NormalizedSignal,
    t_link: Decimal,
) -> dict[str, Any]:
    """What the interpreter proposed, kept for the review card and the audit (F077)."""
    target = hint.target
    if not isinstance(target, uuid.UUID):
        return {"rule": "unresolved", "hint": str(target)}
    proposal: dict[str, Any] = {
        "target": str(target),
        "confidence": None if hint.confidence is None else str(hint.confidence),
        "by": str(method),
    }
    if target not in candidates:
        proposal["refused"] = (
            "not in the lane's book" if target not in ctx.book else "not a resolver candidate"
        )
    elif hint.confidence is None or hint.confidence < t_link:
        proposal["refused"] = "below t_link"
    elif (conflict := inconsistency(ctx.book[target], signal)) is not None:
        proposal["refused"] = conflict
    rule = "unresolved" if "refused" in proposal else "model_proposed"
    return {"rule": rule, "proposal": proposal}


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
    instrument = (
        "stock"
        if signal.msg_type in (MsgType.STOCK_EXIT, MsgType.STOCK_ENTRY)
        else (signal.instrument)
    )
    known = position.instrument is not None  # a setup may not have said
    if instrument in ("option", "stock") and known and instrument != position.instrument:
        return f"instrument {instrument} vs position {position.instrument}"
    stated = (
        ("underlying", signal.underlying, position.underlying),
        ("expiry", signal.expiry, position.expiry),
        ("strike", signal.strike, position.strike),
        ("right", signal.right, position.option_right),
    )
    for name, said, held in stated:
        if said is not None and held is not None and said != held:
            return f"{name} {said} vs position {held}"
    return None


def _fits_setup(setup: PositionState, signal: NormalizedSignal) -> bool:
    """An entry on the setup's underlying that contradicts nothing the setup named."""
    return signal.underlying == setup.underlying and inconsistency(setup, signal) is None


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
