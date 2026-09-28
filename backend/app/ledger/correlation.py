"""Signal -> provider position correlation (FR-3, 7.3 step 1, TRD §8.1, §4.4): pure, no SQL.

Order: entries match by contract; follow-ups use the reply parent's link (same provider only),
then the interpreter's hint above `t_link`; anything else is an explicit AMBIGUOUS / NONE that
becomes a review card. Nothing upgrades ambiguity, not even a single open position (PRD §10).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.interpret.models import LinkMethod, LinkOutcome, MsgType
from app.interpret.signal import LinkHint
from app.ledger.positions import OPENING, PositionState
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
class ParentLink:
    """A current link of one of the reply parent's signals (any author)."""

    provider_id: int
    position_id: uuid.UUID


@dataclass(frozen=True)
class LinkInputs:
    provider_id: int
    book: Mapping[uuid.UUID, PositionState]  # this provider's open/trimmed positions, in order
    parent_links: tuple[ParentLink, ...] = ()


@dataclass(frozen=True)
class LinkDecision:
    """`position_id` is None for `new_position` until apply (F026) creates the row."""

    outcome: LinkOutcome
    method: LinkMethod
    confidence: Decimal | None
    position_id: uuid.UUID | None = None
    candidate_position_ids: tuple[uuid.UUID, ...] = ()


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
                return LinkDecision(LinkOutcome.LINKED, LinkMethod.CODE, _ONE, position_id)
        return LinkDecision(LinkOutcome.NEW_POSITION, LinkMethod.CODE, _ONE)
    if signal.msg_type not in FOLLOW_UPS:
        return None

    # A reply to another author's message is read-only context: never linked across (AC-18).
    replied = {
        link.position_id
        for link in ctx.parent_links
        if link.provider_id == ctx.provider_id and link.position_id in ctx.book
    }
    if len(replied) == 1:
        return LinkDecision(LinkOutcome.LINKED, LinkMethod.REPLY, _ONE, replied.pop())

    if not ctx.book:
        return LinkDecision(LinkOutcome.NONE, hint_method, hint.confidence)
    if (
        isinstance(hint.target, uuid.UUID)
        and hint.target in ctx.book
        and hint.confidence is not None
        and hint.confidence >= t_link
    ):
        return LinkDecision(LinkOutcome.LINKED, hint_method, hint.confidence, hint.target)
    return LinkDecision(
        LinkOutcome.AMBIGUOUS,
        hint_method,
        hint.confidence,
        candidate_position_ids=tuple(ctx.book),
    )


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
