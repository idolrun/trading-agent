"""The one output contract every interpreter (stub, Jev, any LLM) must produce (TRD §4.8 rule 1).

Every field value carries its provenance (PRD §6, C31): `literal` (an exact span of the
message, verified by code — spans.py), `context` (taken from a specific earlier message),
`resolved` (computed by code, e.g. "friday" -> a date, or a fast-path rule) or `model` (a
model's judgment with no span behind it, F071: message type, call/put basis, flags, a size tier
read from the provider's lexicon). Plain fields (msg_type, instrument, exit_fraction,
stop_basis, flags) record theirs in `ParsedSignal.field_sources`.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.interpret.models import MsgType

SCHEMA_VERSION = 1


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")  # hallucinated keys are schema errors


class LiteralSpan(_Contract):
    kind: Literal["literal"] = "literal"
    raw: str  # exact text from the message
    start: int | None = None  # filled by verify_literal_spans when omitted
    end: int | None = None
    candidate_id: str | None = None  # Jev candidate that produced it (F016)


class ContextSource(_Contract):
    kind: Literal["context"] = "context"
    source_message_id: int  # must be in the C71 context set (checked in F032/F033)
    raw: str | None = None


class ResolvedSource(_Contract):
    kind: Literal["resolved"] = "resolved"
    rule: str  # e.g. "expiry_weekday"
    inputs: dict[str, str] = {}


class ModelSource(_Contract):
    """A model's judgment (F071): never a value copied from text, never code-derived."""

    kind: Literal["model"] = "model"
    interpreter: Literal["jev", "laya", "llm"]
    question: str  # the Jev question id, or the LLM output field
    confidence: Decimal | None = None


Provenance = Annotated[
    LiteralSpan | ContextSource | ResolvedSource | ModelSource, Field(discriminator="kind")
]


class F[T](_Contract):
    """A field value plus where it came from."""

    value: T
    source: Provenance


ExitFraction = Literal["all", "half", "third", "quarter", "other_stated", "not_stated"]


class SignalFields(_Contract):
    underlying: F[str] | None = None
    expiry_token: F[str] | None = None
    strike: F[Decimal] | None = None
    right: F[Literal["call", "put"]] | None = None
    entry_price: F[Decimal] | None = None
    entry_price_low: F[Decimal] | None = None
    entry_price_high: F[Decimal] | None = None
    price_unit_hint: Literal["per_share", "per_contract"] | None = None  # "/con" seen (FR-12)
    size_tier: F[str] | None = None
    qty_stated: F[int] | None = None
    stop_level: F[Decimal] | None = None
    stop_basis: Literal["premium", "underlying"] | None = None
    candle_close: bool = False
    tp_levels: list[F[Decimal]] = []
    exit_fraction: ExitFraction | None = None
    breakeven_stop: bool = False
    stop_removed: bool = False
    is_add: bool = False
    prose_condition: str | None = None  # shown, never enforced (C30)
    correction_target_message_id: int | None = None


class LinkHint(_Contract):
    target: uuid.UUID | Literal["new_position", "ambiguous"] | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)


class ParsedSignal(_Contract):
    msg_type: MsgType
    instrument: Literal["option", "stock", "unclear"] | None = None
    fields: SignalFields = SignalFields()
    link_hint: LinkHint = LinkHint()
    field_confidence: dict[str, Decimal] = {}
    # F071: where the plain fields came from (msg_type, instrument, exit_fraction, stop_basis,
    # candle_close, breakeven_stop, stop_removed, is_add); keys absent = not set
    field_sources: dict[str, Provenance] = {}


class ParsedSignals(_Contract):
    """0..n signals per message event (FR-6)."""

    signals: list[ParsedSignal]
    interpreter: Literal["fast", "jev", "laya", "llm", "stub", "none"]
    schema_version: int = SCHEMA_VERSION


def parsed_signals_json_schema() -> dict[str, Any]:
    """JSON schema for LLM structured output (F032)."""
    return ParsedSignals.model_json_schema()
