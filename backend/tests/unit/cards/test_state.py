"""F025: every legal card transition passes; everything else raises."""

import itertools

import pytest

from app.cards.models import CardReason, CardState
from app.cards.state import TRANSITIONS, IllegalTransition, check_transition


def test_legal_transitions() -> None:
    for (from_state, to_state), reasons in TRANSITIONS.items():
        for reason in reasons:
            check_transition(from_state, to_state, reason)


def test_everything_else_is_illegal() -> None:
    states: list[CardState | None] = [None, *CardState]
    for from_state, to_state, reason in itertools.product(states, CardState, CardReason):
        if reason in TRANSITIONS.get((from_state, to_state), frozenset()):
            continue
        with pytest.raises(IllegalTransition):
            check_transition(from_state, to_state, reason)


def test_terminal_states_and_approved_cannot_expire() -> None:
    for terminal in (
        CardState.REJECTED,
        CardState.EXPIRED,
        CardState.INVALIDATED,
        CardState.SUBMITTED,
        CardState.DISABLED,
    ):
        assert not any(src == terminal for src, _ in TRANSITIONS)
    with pytest.raises(IllegalTransition):  # approved + timer: approval already won
        check_transition(CardState.APPROVED, CardState.EXPIRED, CardReason.TIMEOUT)
