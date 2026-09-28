"""Literal-span verification (FR-7, AC-11): every `literal` value must be an exact span of the
message AND the parse of that span. Any error rejects the whole message (review card path);
no partial acceptance, no retry with another model (TRD §4.8 rule 3). No auto-correction (C78):
"raw 112, value 115" is an error, not a fix.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.interpret.signal import F, LiteralSpan, ParsedSignals, SignalFields

TEXT_FIELDS = {"underlying", "expiry_token", "size_tier"}
DECIMAL_FIELDS = {
    "strike",
    "entry_price",
    "entry_price_low",
    "entry_price_high",
    "stop_level",
    "tp_levels",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")


@dataclass(frozen=True)
class SpanError:
    signal_index: int
    field: str
    reason: str


def verify_literal_spans(parsed: ParsedSignals, text: str) -> list[SpanError]:
    """Check every literal field; fills missing `start`/`end` with the first occurrence."""
    errors: list[SpanError] = []
    for i, signal in enumerate(parsed.signals):
        for name, field in _literal_fields(signal.fields):
            if (reason := _check(name, field, text)) is not None:
                errors.append(SpanError(i, name, reason))
    return errors


def _literal_fields(fields: SignalFields) -> list[tuple[str, F[Any]]]:
    found: list[tuple[str, F[Any]]] = []
    for name in SignalFields.model_fields:
        value = getattr(fields, name)
        items = value if isinstance(value, list) else [value]
        found += [
            (name if len(items) == 1 else f"{name}[{n}]", item)
            for n, item in enumerate(items)
            if isinstance(item, F) and isinstance(item.source, LiteralSpan)
        ]
    return found


def _check(name: str, field: F[Any], text: str) -> str | None:
    span = field.source
    assert isinstance(span, LiteralSpan)  # noqa: S101 — filtered by _literal_fields
    if not span.raw:
        return "empty literal"
    if span.start is None and span.end is None:
        start = text.find(span.raw)
        if start < 0:
            return f"literal {span.raw!r} does not occur in the message"
        span.start, span.end = start, start + len(span.raw)
    elif span.start is None or span.end is None or text[span.start : span.end] != span.raw:
        return f"span {span.start}:{span.end} does not match literal {span.raw!r}"
    base = name.split("[")[0]
    parsed = parse_raw(base, span.raw)
    if parsed is None:
        return f"literal {span.raw!r} cannot be read as {base}"
    value = _norm(field.value) if base in TEXT_FIELDS else field.value
    if parsed != value:
        return f"value {field.value!r} is not what {span.raw!r} says ({parsed!r})"
    return None


def parse_raw(field: str, raw: str) -> object | None:
    """The value a literal span denotes for `field`, or None if it denotes none."""
    if field in TEXT_FIELDS:
        return _norm(raw) or None
    if field in DECIMAL_FIELDS:
        numbers = _NUMBER.findall(_THOUSANDS.sub("", raw))
        return Decimal(numbers[0]) if len(numbers) == 1 else None
    if field == "qty_stated":
        numbers = _NUMBER.findall(raw)
        return int(numbers[0]) if len(numbers) == 1 and "." not in numbers[0] else None
    if field == "right":
        return _right(raw)
    return None


def _norm(text: str) -> str:
    """Case, surrounding space and a leading `$` do not change what a word says ($googl)."""
    return " ".join(text.strip().lstrip("$").split()).casefold()


def _right(raw: str) -> str | None:
    s = raw.strip().lower()
    if re.search(r"\bcalls?\b", s) or s == "c" or re.fullmatch(r"[\d.$]*\d\s*c", s):
        return "call"
    if re.search(r"\bputs?\b", s) or s == "p" or re.fullmatch(r"[\d.$]*\d\s*p", s):
        return "put"
    return None
