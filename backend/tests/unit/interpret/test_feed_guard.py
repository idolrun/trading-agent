"""F079: a model's "chatter" never silences a message that could act on an open position."""

import uuid

import pytest

from app.interpret.candidates import find_candidates
from app.interpret.feed_guard import feed_block, provider_terms
from app.interpret.jev.mapping import map_answers
from app.interpret.route import route
from tests.jev_helpers import CFG, c, example_answers
from tests.llm_helpers import ctx

OPEN = [(uuid.uuid4(), "SPY 765P exp 2026-10-02, entered 0.54 at 2026-10-02 09:41:00 ET")]


def block(text: str, book: list[tuple[uuid.UUID, str]] = OPEN, **profile: list[str]) -> str | None:
    context = ctx(text, book=book).model_copy(update={"provider_profile": profile})
    return feed_block(text, context, find_candidates(text))


@pytest.mark.parametrize(
    "text",
    [
        "gotta go here",  # Jev: chatter 0.95 (2026-09-30 replay)
        "pop it here @everyone",  # Jev: chatter 0.83
        "Stopped last 3 have a good weekend @everyone",
        "done",
        "taking the rest",
        "out",
        "runners .63 @everyone",
        "**32%** more @everyone",
        "1350/con @here",
        "Recent high or breakeven on rest @everyone",
        "back in",
    ],
)
def test_implicit_instructions_with_a_position_open_are_blocked(text: str) -> None:
    assert block(text) is not None, text


@pytest.mark.parametrize(
    "text",
    [
        "good morning everyone",
        "Will be late tomorrow, celebrating brothers bday @everyone",
        "@Paulongerrr (Admin)",
        "lol",
        "SPY looks strong",  # a view, not an instruction
    ],
)
def test_plain_chatter_stays_feed_only(text: str) -> None:
    assert block(text) is None, text


def test_nothing_is_blocked_without_a_position() -> None:
    assert block("gotta go here", book=[]) is None


def test_pings_are_not_words() -> None:
    """ "@here" is a ping, so "nice @here" says nothing about leaving."""
    assert block("nice move @here") is None


def test_a_provider_term_from_the_lexicon_is_evidence() -> None:
    lexicon = ["'yeet' means sell everything", "'.95' means $0.95"]
    assert provider_terms({"lexicon": lexicon}) == ["yeet", ".95"]
    assert block("yeet", lexicon=lexicon) == "provider term 'yeet'"
    assert block("yeet") is None


def test_a_blocked_chatter_verdict_is_a_semantic_doubt_not_feed_only() -> None:
    text = "gotta go here"
    context = ctx(text, book=OPEN)
    answers = example_answers(msg_type=c("chatter", 0.95), ticker=c("not_stated"))
    answers = {k: v for k, v in answers.items() if not k.startswith("role_")}
    result = map_answers(answers, context, find_candidates(text), CFG)
    assert route(result, CFG).path == "feed_only"  # without the guard: silenced
    decision = route(result, CFG, feed_block(text, context, find_candidates(text)))
    assert (decision.path, decision.kind) == ("slow_path", "semantic")
    assert decision.reasons[0].startswith("feed_guard: chatter but leaving 'gotta go here'")
