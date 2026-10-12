"""Laya wire format <-> the SDK answers `map_answers` reads; the compact state."""

import uuid

import pytest

from app.interpret.candidates import find_candidates
from app.interpret.jev.questions import build_questions
from app.interpret.laya.convert import to_answers, to_laya_questions
from app.interpret.laya.state import MAX_BOOK, RECENT, build_laya_state, trim_context
from app.interpret.llm_extract.middleware import ScopeViolation
from tests.jev_helpers import CFG, EXAMPLE, c, example_answers, n
from tests.laya_helpers import raw_answers
from tests.llm_helpers import ctx


def test_choice_confidence_is_the_top_probability_not_laya_entropy_score() -> None:
    raw = {
        "msg_type": {
            "type": "choice",
            "choice": "entry",
            "confidence": 0.12,  # normalized entropy of a 90/10 split over 2 labels would be ~0.53
            "probabilities": {"entry": 0.9, "chatter": 0.1},
        }
    }
    [answer] = to_answers(raw).values()
    assert answer.confidence == 0.9  # type: ignore[union-attr]


def test_round_trip_of_every_answer_kind() -> None:
    answers = example_answers(candle_close=n(0.3), msg_type=c("trim", 0.7, soft_exit=0.2))
    back = to_answers(raw_answers(answers))
    assert back["candle_close"].noul == 0.3  # type: ignore[union-attr]
    assert back["msg_type"].choice == "trim"  # type: ignore[union-attr]
    assert back["exit_intent"].score == 0  # type: ignore[union-attr]  # int-keyed legend survives


def test_questions_become_plain_laya_dicts() -> None:
    context = ctx(EXAMPLE)
    questions = to_laya_questions(build_questions(context, find_candidates(EXAMPLE)))
    assert questions["msg_type"]["type"] == "choice"
    assert isinstance(questions["msg_type"]["criteria"], dict)
    assert questions["candle_close"]["type"] == "noul"
    assert questions["exit_intent"]["type"] == "score"
    assert isinstance(questions["exit_intent"]["criteria"], list)


def test_state_keeps_the_jev_paths_and_trims_context() -> None:
    recent = [(str(i), f"earlier message {i}") for i in range(10)]
    context = ctx(EXAMPLE, recent=recent)
    small = trim_context(context)
    assert len(small.recent_author_messages) == RECENT
    state = build_laya_state(small)
    assert state["message"] == {"text": EXAMPLE}
    assert [m["id"] for m in state["recent_author_messages"]] == ["7", "8", "9"]
    assert "candidates" not in state and "provider_profile" not in state


def test_state_carries_the_book_and_a_large_book_is_detectable() -> None:
    book = [(uuid.uuid4(), f"SPY {770 + i}C") for i in range(MAX_BOOK + 1)]
    context = ctx(EXAMPLE, book=book)
    assert len(context.provider_book) > MAX_BOOK
    assert len(build_laya_state(context)["provider_book"]) == MAX_BOOK + 1


def test_state_refuses_user_lot_ids() -> None:
    lot = "11111111-2222-3333-4444-555555555555"
    with pytest.raises(ScopeViolation):
        build_laya_state(ctx(f"spy 770c {lot}"), frozenset({lot}))


def test_the_example_still_assembles_through_the_laya_conversion() -> None:
    from app.interpret.jev.mapping import map_answers

    answers = to_answers(raw_answers(example_answers()))
    result = map_answers(answers, ctx(EXAMPLE), find_candidates(EXAMPLE), CFG, interpreter="laya")
    assert result.parsed is not None and result.parsed.interpreter == "laya"
