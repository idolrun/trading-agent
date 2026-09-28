"""F032: model output → verified `ParsedSignals` (FR-7, AC-11, C31)."""

import uuid
from decimal import Decimal

from app.interpret.llm_extract.middleware import scope_violation
from app.interpret.llm_extract.schema import LlmParsedSignals, to_parsed_signals
from app.interpret.models import MsgType
from app.interpret.signal import ContextSource, LiteralSpan
from tests.llm_helpers import ctx

P1 = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


def parse(signals: list[dict[str, object]]) -> LlmParsedSignals:
    return LlmParsedSignals.model_validate({"signals": signals})


def test_multi_signal_message_gives_two_signals_with_literal_spans() -> None:
    text = "out of IWM, adding AAPL 190c .80"
    out = parse(
        [
            {"msg_type": "full_exit", "underlying": {"raw": "IWM"}, "exit_fraction": "all"},
            {
                "msg_type": "entry",
                "instrument": "option",
                "underlying": {"raw": "AAPL"},
                "strike": {"raw": "190c"},
                "right": {"raw": "190c"},
                "entry_price": {"raw": ".80"},
                "links_to": "new_position",
            },
        ]
    )
    parsed, errors = to_parsed_signals(out, ctx(text))
    assert errors == []
    exit_, entry = parsed.signals
    assert exit_.msg_type is MsgType.FULL_EXIT and entry.msg_type is MsgType.ENTRY
    f = entry.fields
    assert f.strike is not None and f.strike.value == Decimal(190)
    assert f.right is not None and f.right.value == "call"
    assert f.entry_price is not None and f.entry_price.value == Decimal("0.80")
    assert isinstance(f.entry_price.source, LiteralSpan)
    assert text[f.entry_price.source.start : f.entry_price.source.end] == ".80"  # type: ignore[misc]
    assert entry.link_hint.target == "new_position" and parsed.interpreter == "llm"


def test_context_inferred_expiry_carries_its_source_message() -> None:
    c = ctx("spy 770c .95", recent=[("975", "watching spy 770c for fri")])
    out = parse(
        [
            {
                "msg_type": "entry",
                "underlying": {"raw": "spy"},
                "strike": {"raw": "770c"},
                "expiry_token": {"raw": "fri", "source_message_id": "975"},
            }
        ]
    )
    parsed, errors = to_parsed_signals(out, c)
    assert errors == []
    expiry = parsed.signals[0].fields.expiry_token
    assert expiry is not None and expiry.value == "fri"
    assert expiry.source == ContextSource(source_message_id=975, raw="fri")


def test_invented_or_misplaced_values_reject_the_whole_answer() -> None:
    c = ctx("spy 770c .95", recent=[("975", "watching spy")])
    cases = [
        {"msg_type": "entry", "strike": {"raw": "771c"}},  # not in the text
        {"msg_type": "entry", "strike": {"raw": "strike"}},  # not a number
        {"msg_type": "entry", "expiry_token": {"raw": "fri", "source_message_id": "975"}},
        {"msg_type": "entry", "expiry_token": {"raw": "fri", "source_message_id": "999"}},
        {"msg_type": "correction", "correction_target_message_id": "999"},
    ]
    for signal in cases:
        _, errors = to_parsed_signals(parse([signal]), c)
        assert errors, signal


def test_unknown_position_id_is_an_ambiguous_link_not_a_guess() -> None:
    c = ctx("trim half", book=[(P1, "QQQ 480P exp 2026-09-28")])
    known, _ = to_parsed_signals(parse([{"msg_type": "trim", "links_to": str(P1)}]), c)
    assert known.signals[0].link_hint.target == P1
    unknown, _ = to_parsed_signals(parse([{"msg_type": "trim", "links_to": "P9"}]), c)
    assert unknown.signals[0].link_hint.target == "ambiguous"


def test_scope_guard_matches_portfolio_keys_and_lot_ids_only() -> None:
    lot = "5f0c7a52-0000-4000-8000-000000000001"
    assert scope_violation('{"lot": {"qty": 3}}', frozenset())
    assert scope_violation('{\\"Balance\\": 1200}', frozenset())  # escaped inside a string
    assert scope_violation(f"close {lot} now", frozenset({lot}))
    assert scope_violation("lotto size, balance the book, pnl was great", frozenset()) is None
