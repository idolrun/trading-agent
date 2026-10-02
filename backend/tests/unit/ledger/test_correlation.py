"""F021/F069: correlation decision table, the deterministic cross-check of model hints, and
TRD §12 sequencing rows (AC-18 linking clause)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.interpret.models import LinkMethod, LinkOutcome
from app.interpret.signal import LinkHint
from app.ledger.correlation import LinkDecision, LinkInputs, resolve_link
from app.ledger.positions import PositionState
from app.ledger.resolver import Resolution
from app.validate.normalize import NormalizedSignal

A, B = 7, 8
EXP = date(2026, 10, 2)
GOOGL = PositionState(
    instrument="option",
    underlying="GOOGL",
    expiry=EXP,
    strike=Decimal("357.5"),
    option_right="call",
)
META = PositionState(
    instrument="option", underlying="META", expiry=EXP, strike=Decimal("700"), option_right="put"
)
P_GOOGL, P_META, P_B = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NO_HINT = LinkHint()


def resolve(
    msg_type: str,
    book: dict[uuid.UUID, PositionState],
    hint: LinkHint = NO_HINT,
    resolution: Resolution | None = None,
    **fields: Any,
) -> LinkDecision | None:
    signal = NormalizedSignal(
        msg_type=msg_type, instrument=fields.pop("instrument", "option"), **fields
    )
    return resolve_link(
        signal,
        hint,
        LinkInputs(provider_id=A, book=book, resolution=resolution),
        t_link=Decimal("0.8"),
        hint_method=LinkMethod.JEV,
    )


def test_take_half_off_linked_by_hint() -> None:
    d = resolve("trim", {P_GOOGL: GOOGL}, LinkHint(target=P_GOOGL, confidence=Decimal("0.93")))
    assert d == LinkDecision(LinkOutcome.LINKED, LinkMethod.JEV, Decimal("0.93"), P_GOOGL)


def test_ambiguous_hint_lists_whole_book() -> None:
    d = resolve("trim", {P_GOOGL: GOOGL, P_META: META}, LinkHint(target="ambiguous"))
    assert d is not None and d.outcome == LinkOutcome.AMBIGUOUS
    assert d.candidate_position_ids == (P_GOOGL, P_META) and d.position_id is None


def test_low_confidence_is_ambiguous_even_with_one_position() -> None:
    d = resolve("trim", {P_GOOGL: GOOGL}, LinkHint(target=P_GOOGL, confidence=Decimal("0.6")))
    assert d is not None and d.outcome == LinkOutcome.AMBIGUOUS
    assert d.candidate_position_ids == (P_GOOGL,)


def test_hint_outside_book_is_ambiguous() -> None:
    d = resolve("full_exit", {P_GOOGL: GOOGL}, LinkHint(target=P_B, confidence=Decimal("0.99")))
    assert d is not None and d.outcome == LinkOutcome.AMBIGUOUS


def test_empty_book_is_none() -> None:
    d = resolve("stop_move", {}, LinkHint(target="ambiguous", confidence=Decimal("0.4")))
    assert d == LinkDecision(LinkOutcome.NONE, LinkMethod.JEV, Decimal("0.4"))


def reply(position_id: uuid.UUID) -> Resolution:
    return Resolution(
        outcome="unique",
        position_id=position_id,
        candidates=(position_id,),
        method=LinkMethod.REPLY,
    )


def test_reply_to_own_entry_beats_hint() -> None:
    d = resolve(
        "trim",
        {P_GOOGL: GOOGL, P_META: META},
        LinkHint(target=P_META, confidence=Decimal("0.99")),
        resolution=reply(P_GOOGL),
    )
    assert d == LinkDecision(LinkOutcome.LINKED, LinkMethod.REPLY, Decimal(1), P_GOOGL)


def test_reply_pick_contradicted_by_the_message_is_ambiguous() -> None:
    d = resolve(
        "trim", {P_GOOGL: GOOGL, P_META: META}, resolution=reply(P_GOOGL), underlying="META"
    )
    assert d is not None and d.outcome == LinkOutcome.AMBIGUOUS
    assert d.evidence is not None and "underlying META" in d.evidence["conflict"]


def test_hint_contradicting_the_stated_ticker_is_refused() -> None:
    """F069: the message says META, the model picks GOOGL at 0.99: review, never a link."""
    d = resolve(
        "trim",
        {P_GOOGL: GOOGL, P_META: META},
        LinkHint(target=P_GOOGL, confidence=Decimal("0.99")),
        underlying="META",
    )
    assert d is not None and d.outcome == LinkOutcome.AMBIGUOUS
    assert d.evidence is not None and d.evidence["hint_rejected"].startswith("underlying META")


def test_hint_outside_the_resolver_candidates_is_refused() -> None:
    narrowed = Resolution(outcome="ambiguous", candidates=(P_META,))
    d = resolve(
        "trim",
        {P_GOOGL: GOOGL, P_META: META},
        LinkHint(target=P_GOOGL, confidence=Decimal("0.99")),
        resolution=narrowed,
    )
    assert d is not None and d.outcome == LinkOutcome.AMBIGUOUS
    assert d.candidate_position_ids == (P_META,)


def test_hint_with_wrong_strike_or_right_is_refused() -> None:
    hint = LinkHint(target=P_GOOGL, confidence=Decimal("0.99"))
    assert resolve("trim", {P_GOOGL: GOOGL}, hint, strike=Decimal(360)).outcome == "ambiguous"  # type: ignore[union-attr]
    assert resolve("trim", {P_GOOGL: GOOGL}, hint, right="put").outcome == "ambiguous"  # type: ignore[union-attr]
    stock = resolve("stock_exit", {P_GOOGL: GOOGL}, hint, instrument=None)
    assert stock is not None and stock.outcome == LinkOutcome.AMBIGUOUS


def test_consistent_hint_is_linked() -> None:
    d = resolve(
        "trim",
        {P_GOOGL: GOOGL, P_META: META},
        LinkHint(target=P_GOOGL, confidence=Decimal("0.9")),
        underlying="GOOGL",
    )
    assert d == LinkDecision(LinkOutcome.LINKED, LinkMethod.JEV, Decimal("0.9"), P_GOOGL)


def test_user_choice_overrules_a_misread_field() -> None:
    d = resolve("trim", {P_GOOGL: GOOGL}, resolution=Resolution.chosen(P_GOOGL), underlying="GOOG")
    assert d is not None and (d.outcome, d.method) == (
        LinkOutcome.LINKED,
        LinkMethod.USER_CORRECTION,
    )
    assert d.evidence is not None and "conflict" in d.evidence


def test_entry_on_open_contract_is_add() -> None:
    d = resolve(
        "entry",
        {P_META: META, P_GOOGL: GOOGL},
        underlying="GOOGL",
        expiry=EXP,
        strike=Decimal("357.50"),
        right="call",
    )
    assert d == LinkDecision(LinkOutcome.LINKED, LinkMethod.CODE, Decimal(1), P_GOOGL)


def test_entry_on_other_strike_is_new_position() -> None:
    d = resolve(
        "entry",
        {P_GOOGL: GOOGL},
        underlying="GOOGL",
        expiry=EXP,
        strike=Decimal("360"),
        right="call",
    )
    assert d == LinkDecision(LinkOutcome.NEW_POSITION, LinkMethod.CODE, Decimal(1))


def test_stock_entry_matches_stock_position_only() -> None:
    stock = PositionState(instrument="stock", underlying="GOOGL")
    p_stock = uuid.uuid4()
    d = resolve(
        "stock_entry", {P_GOOGL: GOOGL, p_stock: stock}, underlying="GOOGL", instrument=None
    )
    assert d is not None and d.position_id == p_stock


def test_non_linkable_types_return_none() -> None:
    assert resolve("commentary_pnl", {P_GOOGL: GOOGL}) is None
    assert resolve("watch", {P_GOOGL: GOOGL}) is None


def test_b_same_reply_gets_own_position() -> None:
    """TRD §12: B replies 'same' to A1 -> B's entry opens B's position; A untouched."""
    signal = NormalizedSignal(
        msg_type="entry",
        instrument="option",
        underlying="GOOGL",
        expiry=EXP,
        strike=Decimal("357.5"),
        right="call",
    )
    d = resolve_link(
        signal,
        LinkHint(),
        LinkInputs(provider_id=B, book={}),
        t_link=Decimal("0.8"),
        hint_method=LinkMethod.LLM,
    )
    assert d == LinkDecision(LinkOutcome.NEW_POSITION, LinkMethod.CODE, Decimal(1))
