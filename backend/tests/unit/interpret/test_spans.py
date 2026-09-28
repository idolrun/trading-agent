"""F015: literal spans (AC-11) and the signal contract."""

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.interpret.signal import (
    ContextSource,
    F,
    LiteralSpan,
    ParsedSignal,
    ParsedSignals,
    ResolvedSource,
    SignalFields,
    parsed_signals_json_schema,
)
from app.interpret.spans import parse_raw, verify_literal_spans

TEXT = "Bought GOOGL 357.5C @ 1.67 exp .95 tp 2.10/2.50 140/con size lotto 3x"


def lit(value: Any, raw: str, start: int | None = None, end: int | None = None) -> F[Any]:
    return F(value=value, source=LiteralSpan(raw=raw, start=start, end=end))


def one(**fields: Any) -> ParsedSignals:
    return ParsedSignals(
        signals=[
            ParsedSignal(msg_type="entry", instrument="option", fields=SignalFields(**fields))
        ],
        interpreter="llm",
    )


def errors(text: str = TEXT, **fields: Any) -> list[str]:
    return [e.reason for e in verify_literal_spans(one(**fields), text)]


def test_exact_spans_pass_and_offsets_are_filled() -> None:
    parsed = one(
        underlying=lit("GOOGL", "GOOGL"),
        strike=lit(Decimal("357.5"), "357.5C"),
        right=lit("call", "357.5C"),
        entry_price=lit(Decimal("1.67"), "1.67"),
        tp_levels=[lit(Decimal("2.10"), "2.10"), lit(Decimal("2.5"), "2.50")],
        size_tier=lit("lotto", "lotto"),
        qty_stated=lit(3, "3x"),
    )
    assert verify_literal_spans(parsed, TEXT) == []
    span = parsed.signals[0].fields.underlying.source  # type: ignore[union-attr]
    assert isinstance(span, LiteralSpan) and (span.start, span.end) == (7, 12)


def test_given_offsets_must_match_exactly() -> None:
    assert errors(underlying=lit("GOOGL", "GOOGL", 7, 12)) == []
    assert errors(underlying=lit("GOOGL", "GOOGL", 6, 11))  # off by one
    assert errors(underlying=lit("GOOGL", "GOOGL", 7, None))


def test_raw_absent_from_text_is_rejected() -> None:
    assert errors(underlying=lit("META", "META"))
    assert errors(underlying=lit("googl", "googl"))  # case-sensitive substring


def test_value_must_be_the_parse_of_raw() -> None:
    text = "Bought TSLA 112C"
    assert errors(text, strike=lit(Decimal(115), "112"))  # no auto-correction (C78)
    assert errors(text, strike=lit(Decimal(112), "112")) == []
    assert errors(text, right=lit("put", "112C"))


@pytest.mark.parametrize(
    ("field", "raw", "value"),
    [
        ("entry_price", ".95", Decimal("0.95")),
        ("entry_price", "140/con", Decimal(140)),  # scale is F018's job
        ("strike", "770c", Decimal(770)),
        ("strike", "$1,250", Decimal(1250)),
        ("right", "770c", "call"),
        ("right", "puts", "put"),
        ("right", "P", "put"),
        ("underlying", "$googl", "googl"),
        ("qty_stated", "3x", 3),
    ],
)
def test_parse_raw(field: str, raw: str, value: object) -> None:
    assert parse_raw(field, raw) == value


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("entry_price", "1.67-1.70"),  # two numbers: use entry_price_low/high spans
        ("entry_price", "free"),
        ("right", "GOOGL"),
        ("qty_stated", "1.5"),
        ("underlying", "   "),
    ],
)
def test_unreadable_raw(field: str, raw: str) -> None:
    assert parse_raw(field, raw) is None


def test_non_literal_sources_are_not_span_checked() -> None:
    parsed = one(
        underlying=F(value="GOOGL", source=ContextSource(source_message_id=42)),
        expiry_token=F(value="2026-10-02", source=ResolvedSource(rule="expiry_weekday")),
    )
    assert verify_literal_spans(parsed, "Take half off") == []


def test_every_signal_is_checked() -> None:
    parsed = ParsedSignals(
        signals=[
            ParsedSignal(
                msg_type="trim", fields=SignalFields(entry_price=lit(Decimal("2.1"), "2.10"))
            ),
            ParsedSignal(msg_type="stop_set", fields=SignalFields(stop_level=lit(Decimal(9), "9"))),
        ],
        interpreter="llm",
    )
    found = verify_literal_spans(parsed, "Take half @ 2.10, stop 1.50")
    assert [(e.signal_index, e.field) for e in found] == [(1, "stop_level")]


def test_contract_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        ParsedSignals.model_validate({"signals": [], "interpreter": "llm", "confidence": 1})
    with pytest.raises(ValidationError):
        ParsedSignal.model_validate({"msg_type": "entry", "fields": {"ticker": {}}})
    with pytest.raises(ValidationError):
        ParsedSignal.model_validate({"msg_type": "buy"})


def test_json_schema_export() -> None:
    schema = parsed_signals_json_schema()
    assert schema["title"] == "ParsedSignals"
    assert "SignalFields" in schema["$defs"]


@given(st.text(min_size=1, max_size=80), st.data())
def test_any_substring_verifies(text: str, data: st.DataObject) -> None:
    start = data.draw(st.integers(0, len(text) - 1))
    end = data.draw(st.integers(start + 1, len(text)))
    raw = text[start:end]
    if not raw.strip().lstrip("$").strip():
        return  # blank literals are always rejected
    assert errors(text, expiry_token=lit(raw, raw)) == []


@given(st.text(max_size=40), st.text(min_size=1, max_size=10))
def test_raw_not_in_text_always_fails(text: str, raw: str) -> None:
    if raw in text:
        return
    assert errors(text, expiry_token=lit(raw, raw))
