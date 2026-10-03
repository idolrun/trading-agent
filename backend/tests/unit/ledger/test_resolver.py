"""F069: the context resolver's rungs (reply chain, thread, stated contract) on a lane book."""

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.interpret.candidates import find_candidates
from app.interpret.models import LinkMethod
from app.ledger.positions import PositionState
from app.ledger.resolver import Evidence, ParentLink, Resolution, resolve_context

X, Y = 7, 8
EXP = date(2026, 10, 2)


def option(underlying: str, strike: str, right: str) -> PositionState:
    return PositionState(
        instrument="option",
        underlying=underlying,
        expiry=EXP,
        strike=Decimal(strike),
        option_right=right,  # type: ignore[arg-type]
    )


SPY, QQQ, META = (
    option("SPY", "765", "put"),
    option("QQQ", "745", "put"),
    option("META", "740", "call"),
)
P_SPY, P_QQQ, P_META, P_OTHER = (uuid.uuid4() for _ in range(4))
BOOK = {P_SPY: SPY, P_QQQ: QQQ}


def resolve(
    text: str,
    book: dict[uuid.UUID, PositionState] = BOOK,
    hops: Sequence[Sequence[ParentLink]] = (),
    thread: frozenset[uuid.UUID] = frozenset(),
) -> Resolution:
    return resolve_context(
        provider_id=X,
        book=book,
        reply_hops=hops,
        thread_positions=thread,
        evidence=Evidence.of(text, find_candidates(text)),
    )


def test_empty_book_is_none() -> None:
    assert resolve("trim half", {}).outcome == "none"


def test_trim_half_with_two_positions_is_ambiguous() -> None:
    r = resolve("trim half")
    assert (r.outcome, r.candidates) == ("ambiguous", (P_SPY, P_QQQ))


def test_one_position_is_the_sole_candidate() -> None:
    """F077 (user decision 2026-10-03): the lane's only live position resolves by code."""
    r = resolve("trim half", {P_SPY: SPY})
    assert (r.outcome, r.position_id, r.method) == ("unique", P_SPY, LinkMethod.CODE)
    assert r.evidence == {"rule": "sole_candidate"}


def test_unresolved_book_of_one_is_the_sole_candidate() -> None:
    r = Resolution.unresolved({P_SPY: SPY})
    assert (r.outcome, r.position_id, r.evidence["rule"]) == ("unique", P_SPY, "sole_candidate")
    assert Resolution.unresolved(BOOK).outcome == "ambiguous"


def test_explicit_ticker_resolves() -> None:
    r = resolve("trim SPY")
    assert (r.outcome, r.position_id, r.method) == ("unique", P_SPY, LinkMethod.CONTRACT)
    assert r.evidence["tickers"] == ["SPY"]


def test_cashtag_and_lowercase_ticker() -> None:
    assert resolve("out $qqq").position_id == P_QQQ


def test_strike_shaped_token_resolves_without_a_ticker() -> None:
    r = resolve("765p out")
    assert (r.outcome, r.position_id) == ("unique", P_SPY)


def test_a_bare_number_without_a_ticker_is_not_a_strike() -> None:
    assert resolve("sold 745 here").outcome == "ambiguous"  # could be a price


def test_ticker_and_strike_split_two_positions_on_one_underlying() -> None:
    spy_770 = option("SPY", "770", "put")
    p_770 = uuid.uuid4()
    book = {P_SPY: SPY, p_770: spy_770}
    assert resolve("trim SPY", book).outcome == "ambiguous"
    assert resolve("trim SPY 770", book).position_id == p_770


def test_calls_narrowing_to_one_is_the_sole_candidate() -> None:
    r = resolve("trim the calls", {P_SPY: SPY, P_META: META})
    assert (r.outcome, r.position_id) == ("unique", P_META)
    assert r.evidence == {"rule": "sole_candidate", "rights": ["call"]}


def test_calls_alone_only_narrows_between_two_calls() -> None:
    meta_750 = option("META", "750", "call")
    r = resolve("trim the calls", {P_SPY: SPY, P_META: META, P_OTHER: meta_750})
    assert (r.outcome, r.candidates) == ("ambiguous", (P_META, P_OTHER))


def test_overfound_words_are_not_evidence() -> None:
    r = resolve("nice trim here")  # "nice" is a ticker candidate that names no position
    assert (r.outcome, r.candidates) == ("ambiguous", (P_SPY, P_QQQ))


def test_reply_to_own_linked_message_resolves() -> None:
    r = resolve("trim half", hops=[[ParentLink(X, P_QQQ)]])
    assert (r.outcome, r.position_id, r.method) == ("unique", P_QQQ, LinkMethod.REPLY)


def test_reply_chain_is_followed_to_the_nearest_linked_ancestor() -> None:
    """entry <- comment (no signal) <- trim: the comment has no link, the entry two hops up."""
    r = resolve("trim half", hops=[[], [ParentLink(X, P_SPY)]])
    assert (r.position_id, r.evidence["depth"]) == (P_SPY, 2)


def test_reply_chain_stops_at_its_depth() -> None:
    r = resolve("trim half", hops=[[], [], []])
    assert r.outcome == "ambiguous"


def test_reply_to_another_provider_is_not_evidence() -> None:
    r = resolve("trim half", hops=[[ParentLink(Y, P_SPY)]])
    assert r.outcome == "ambiguous"


def test_reply_to_a_closed_or_other_lane_position_falls_through() -> None:
    r = resolve("trim half", hops=[[ParentLink(X, P_OTHER)]])
    assert r.outcome == "ambiguous"


def test_reply_contradicted_by_the_stated_ticker_is_ambiguous() -> None:
    r = resolve("trim QQQ", hops=[[ParentLink(X, P_SPY)]])
    assert r.outcome == "ambiguous"
    assert set(r.candidates) == {P_SPY, P_QQQ} and "conflict" in r.evidence


def test_thread_with_one_position_resolves() -> None:
    r = resolve("trim half", thread=frozenset({P_QQQ}))
    assert (r.outcome, r.position_id, r.method) == ("unique", P_QQQ, LinkMethod.THREAD)


def test_thread_with_two_positions_narrows() -> None:
    book = {P_SPY: SPY, P_QQQ: QQQ, P_META: META}
    r = resolve("trim half", book, thread=frozenset({P_SPY, P_META}))
    assert (r.outcome, r.candidates) == ("ambiguous", (P_SPY, P_META))


def test_resolution_round_trips_as_json() -> None:
    r = resolve("trim SPY")
    assert Resolution.model_validate(r.model_dump(mode="json")) == r
