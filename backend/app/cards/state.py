"""Card state machine (TRD §6 + `disabled`, X3; diagram 07 §7.4). Pure: the repo applies it."""

from app.cards.models import CardReason, CardState

S, R = CardState, CardReason
TRANSITIONS: dict[tuple[CardState | None, CardState], frozenset[CardReason]] = {
    (None, S.PENDING): frozenset({R.CREATED}),
    (None, S.DISABLED): frozenset({R.CREATED}),
    (S.PENDING, S.PENDING): frozenset({R.EDITED, R.REVIEW_COMPLETED}),
    (S.PENDING, S.APPROVED): frozenset({R.APPROVED}),
    (S.PENDING, S.REJECTED): frozenset({R.REJECTED}),
    (S.PENDING, S.EXPIRED): frozenset({R.TIMEOUT, R.RESTART}),
    (S.PENDING, S.INVALIDATED): frozenset({R.SUPERSEDED, R.CORRECTION, R.DELETED, R.PRICE_BAND}),
    (S.APPROVED, S.INVALIDATED): frozenset({R.REVALIDATE_FAILED, R.ORDER_REJECTED}),
    (S.APPROVED, S.SUBMITTED): frozenset({R.SUBMITTED}),
}


class IllegalTransition(ValueError):
    """The card already moved on (a decision, timer or supersession won the race) -> 409."""


def check_transition(from_state: CardState | None, to_state: CardState, reason: CardReason) -> None:
    if reason not in TRANSITIONS.get((from_state, to_state), frozenset()):
        raise IllegalTransition(f"card {from_state} -> {to_state} ({reason}) not allowed")
