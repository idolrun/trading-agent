"""What the model returns (`LlmParsedSignals`) and the code that turns it into the one contract.

The model copies text; code decides what the text means (C78, FR-7). A field is only `raw`
text: no value, no offsets. Code parses the value from `raw` (`parse_raw`), finds the span in
the message (`verify_literal_spans`) or checks it against the named context message, and any
failure rejects the whole answer (no partial acceptance, no second model).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.interpret.context import ProviderContext
from app.interpret.models import MsgType
from app.interpret.signal import (
    ContextSource,
    ExitFraction,
    LinkHint,
    LiteralSpan,
    ParsedSignal,
    ParsedSignals,
)
from app.interpret.spans import parse_raw, verify_literal_spans


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmField(_Out):
    raw: str = Field(description="The exact characters copied from the message, unchanged.")
    source_message_id: str | None = Field(
        default=None,
        description="Id of the earlier context message the text was copied from; "
        "null when it is from the current message.",
    )


class LlmSignal(_Out):
    msg_type: MsgType
    instrument: Literal["option", "stock", "unclear"] | None = None
    underlying: LlmField | None = None
    expiry_token: LlmField | None = None
    strike: LlmField | None = None
    right: LlmField | None = None
    entry_price: LlmField | None = None
    entry_price_low: LlmField | None = None
    entry_price_high: LlmField | None = None
    size_tier: LlmField | None = None
    qty_stated: LlmField | None = None
    stop_level: LlmField | None = None
    tp_levels: list[LlmField] = []
    price_unit_hint: Literal["per_share", "per_contract"] | None = None
    stop_basis: Literal["premium", "underlying"] | None = None
    candle_close: bool = False
    exit_fraction: ExitFraction | None = None
    breakeven_stop: bool = False
    stop_removed: bool = False
    is_add: bool = False
    prose_condition: str | None = None
    correction_target_message_id: str | None = None
    links_to: str | None = Field(
        default=None,
        description="Id of the provider position this refers to, new_position, or ambiguous.",
    )

    @field_validator("msg_type")
    @classmethod
    def _not_unparsed(cls, v: MsgType) -> MsgType:
        if v is MsgType.UNPARSED:
            raise ValueError("unparsed is not a model answer")
        return v


class LlmParsedSignals(_Out):
    signals: list[LlmSignal] = Field(
        description="One entry per signal in the message; a single chatter entry if none."
    )


_LITERAL_FIELDS = (
    "underlying",
    "expiry_token",
    "strike",
    "right",
    "entry_price",
    "entry_price_low",
    "entry_price_high",
    "size_tier",
    "qty_stated",
    "stop_level",
)


def context_texts(ctx: ProviderContext) -> dict[str, str]:
    """The C71 set: the only earlier messages a `context` field may cite."""
    msgs = [*ctx.recent_author_messages, *ctx.mentioned_messages]
    if ctx.reply_parent is not None:
        msgs.append(ctx.reply_parent)
    return {m.id: m.text for m in msgs}


def to_parsed_signals(
    out: LlmParsedSignals, ctx: ProviderContext
) -> tuple[ParsedSignals, list[str]]:
    """Convert and verify. Non-empty errors = content rejection (review card, no fallback)."""
    errors: list[str] = []
    sources = context_texts(ctx)

    def field(i: int, name: str, f: LlmField) -> dict[str, Any] | None:
        value = parse_raw(name, f.raw)
        if value is None:
            errors.append(f"signal {i} {name}: {f.raw!r} cannot be read as {name}")
            return None
        if f.source_message_id is None:
            return {"value": value, "source": LiteralSpan(raw=f.raw)}
        text = sources.get(f.source_message_id)
        if text is None or not f.raw or f.raw not in text:
            errors.append(
                f"signal {i} {name}: {f.raw!r} is not in context message {f.source_message_id}"
            )
            return None
        source = ContextSource(source_message_id=int(f.source_message_id), raw=f.raw)
        return {"value": value, "source": source}

    signals: list[ParsedSignal] = []
    for i, s in enumerate(out.signals):
        values: dict[str, Any] = {
            name: field(i, name, f)
            for name in _LITERAL_FIELDS
            if (f := getattr(s, name)) is not None
        }
        tps = [field(i, "tp_levels", f) for f in s.tp_levels]
        target = s.correction_target_message_id
        if target is not None and target not in sources:
            errors.append(f"signal {i}: correction target {target} is not in the context")
        if errors:
            continue
        signals.append(
            ParsedSignal.model_validate(
                dict(
                    msg_type=s.msg_type,
                    instrument=s.instrument,
                    fields=dict(
                        **values,
                        tp_levels=tps,
                        price_unit_hint=s.price_unit_hint,
                        stop_basis=s.stop_basis,
                        candle_close=s.candle_close,
                        exit_fraction=s.exit_fraction,
                        breakeven_stop=s.breakeven_stop,
                        stop_removed=s.stop_removed,
                        is_add=s.is_add,
                        prose_condition=s.prose_condition,
                        correction_target_message_id=None if target is None else int(target),
                    ),
                    link_hint=_link(s.links_to, ctx),
                )
            )
        )
    parsed = ParsedSignals(signals=signals, interpreter="llm")
    if not errors:
        text = ctx.message.text or ""
        errors += [
            f"signal {e.signal_index} {e.field}: {e.reason}"
            for e in verify_literal_spans(parsed, text)
        ]
    return parsed, errors


def _link(links_to: str | None, ctx: ProviderContext) -> LinkHint:
    """A position id the book does not have is not a link: ambiguous (review card, C31)."""
    if links_to is None:
        return LinkHint()
    if links_to in ("new_position", "ambiguous"):
        return LinkHint(target=links_to)
    book = {str(b.id): b.id for b in ctx.provider_book}
    return LinkHint(target=book.get(links_to, "ambiguous"))
