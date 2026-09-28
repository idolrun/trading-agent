"""Jev answers → `ParsedSignals` (TRD Appendix A.3). A field is literal only when Jev picked a
code-found candidate with confidence ≥ `t_field`; its value is parsed from the candidate's exact
span. Anything Jev cannot settle is a slow-path reason, never a guess (C78, FR-10)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from app.interpret.candidates import Candidate, Candidates
from app.interpret.context import ProviderContext
from app.interpret.models import MsgType
from app.interpret.signal import LiteralSpan, ParsedSignal, ParsedSignals, ResolvedSource
from app.interpret.spans import parse_raw, verify_literal_spans
from app.runtime_config.schema import RuntimeConfig

Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer

FEED_TYPES = frozenset({MsgType.CHATTER, MsgType.COMMENTARY_PNL, MsgType.WATCH})
# Contract fields that must be literal before code may assemble (else slow path, A.3 rule 3).
REQUIRED: dict[MsgType, tuple[str, ...]] = {
    MsgType.ENTRY: ("underlying", "strike", "right"),
    MsgType.ADD: ("underlying", "strike", "right"),
    MsgType.STOCK_ENTRY: ("underlying",),
    MsgType.STOCK_EXIT: ("underlying",),
}
# Single-valued number roles → field; two numbers claiming one is a slow-path reason (A.3 rule 2).
SINGLE_ROLES = {
    "strike": "strike",
    "entry_price": "entry_price",
    "entry_price_range_low": "entry_price_low",
    "entry_price_range_high": "entry_price_high",
    "stop_level": "stop_level",
    "share_or_contract_qty": "qty_stated",
}
FLAGS = ("candle_close", "is_add", "breakeven_stop", "stop_removed")
_RIGHT_WORD = re.compile(r"\b(?:calls?|puts?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class JevResult:
    msg_type: MsgType
    msg_type_confidence: Decimal
    signal_count: str
    parsed: ParsedSignals | None  # None: Jev could not settle every field (see slow_reasons)
    slow_reasons: tuple[str, ...] = ()
    hints: dict[str, Any] = field(default_factory=dict)  # compact answers for the slow path
    model_call_id: int | None = None


def _d(p: float) -> Decimal:
    return Decimal(str(round(p, 4)))


def compact(answers: Mapping[str, Answer]) -> dict[str, Any]:
    """What the LLM gets as hints: the pick and its confidence, no full distributions."""
    out: dict[str, Any] = {}
    for name, a in sorted(answers.items()):
        if isinstance(a, ChoiceAnswer):
            out[name] = {"choice": a.choice, "confidence": round(a.confidence, 3)}
        elif isinstance(a, NoulAnswer):
            out[name] = {"yes": round(a.noul, 3)}
        else:
            out[name] = {"score": round(a.score, 2)}
    return out


def map_answers(
    answers: Mapping[str, Answer],
    ctx: ProviderContext,
    candidates: Candidates,
    cfg: RuntimeConfig,
) -> JevResult:
    t_field, t_link = cfg.t_field, cfg.t_link
    reasons: list[str] = []
    text = ctx.message.text or ""

    def choice(name: str) -> ChoiceAnswer:
        a = answers.get(name)
        if not isinstance(a, ChoiceAnswer):
            raise KeyError(f"Jev answer {name!r} missing")  # a request/response mismatch: a bug
        return a

    def noul(name: str) -> NoulAnswer:
        a = answers.get(name)
        if not isinstance(a, NoulAnswer):
            raise KeyError(f"Jev answer {name!r} missing")
        return a

    def confident(name: str, a: ChoiceAnswer) -> bool:
        if _d(a.confidence) < t_field:
            reasons.append(f"{name}: confidence {a.confidence:.2f} < t_field")
            return False
        return True

    mt = choice("msg_type")
    msg_type, conf = MsgType(mt.choice), _d(mt.confidence)
    # C52: "trim or soft exit?" at a middling exit intent is a soft exit, never a sell order.
    exit_intent = answers.get("exit_intent")
    if msg_type in (MsgType.TRIM, MsgType.SOFT_EXIT) and conf < t_field:
        both = _d(mt.probabilities.get("trim", 0) + mt.probabilities.get("soft_exit", 0))
        if (
            both >= t_field
            and isinstance(exit_intent, ScoreAnswer)
            and round(exit_intent.score) == 2
        ):
            msg_type, conf = MsgType.SOFT_EXIT, both
    signal_count = choice("signal_count").choice
    base = JevResult(msg_type, conf, signal_count, parsed=None, hints=compact(answers))
    if msg_type in FEED_TYPES:  # no fields needed; routing decides on t_chat
        parsed = ParsedSignals(signals=[ParsedSignal(msg_type=msg_type)], interpreter="jev")
        return replace(base, parsed=parsed)

    fields: dict[str, Any] = {}
    confidence: dict[str, Decimal] = {}

    def literal(name: str, c: Candidate, p: float) -> None:
        value = parse_raw(name.split("[")[0], c.text)
        if value is None:
            reasons.append(f"{name}: {c.text!r} cannot be read as {name}")
            return
        source = LiteralSpan(raw=c.text, start=c.start, end=c.end, candidate_id=c.id)
        entry = {"value": value, "source": source}
        if name == "tp_levels":
            fields.setdefault("tp_levels", []).append(entry)
        else:
            fields[name] = entry
        confidence[name] = _d(p)

    # Numbers: each candidate's role (A.1 role_<nX>).
    by_role: dict[str, list[tuple[Candidate, float]]] = {}
    for c in candidates.numbers:
        a = choice(f"role_{c.id}")
        if confident(f"role_{c.id}", a):
            by_role.setdefault(a.choice, []).append((c, a.confidence))
    for role, name in SINGLE_ROLES.items():
        found = by_role.get(role, [])
        if len(found) > 1:
            reasons.append(f"{role}: claimed by {', '.join(c.id for c, _ in found)}")
        elif found:
            literal(name, *found[0])
    for c, p in by_role.get("take_profit_level", []):
        literal("tp_levels", c, p)

    # Picks among code-found spans.
    ids = {c.id: c for c in (*candidates.tickers, *candidates.expiry_tokens)}
    for question, name in (("ticker", "underlying"), ("expiry", "expiry_token")):
        a = choice(question)
        if a.choice != "not_stated" and confident(question, a):
            literal(name, ids[a.choice], a.confidence)

    right = choice("right")
    if right.choice != "not_stated" and confident("right", right):
        strike = fields.get("strike", {}).get("source")
        span = _right_span(text, strike, right.choice)
        if span is None:
            reasons.append(f"right: no text in the message says {right.choice}")
        else:
            fields["right"] = {"value": right.choice, "source": span}
            confidence["right"] = _d(right.confidence)

    tier = choice("size_tier")
    if tier.choice != "not_stated" and confident("size_tier", tier):
        m = re.search(rf"\b{re.escape(tier.choice)}\b", text, re.IGNORECASE)
        source: LiteralSpan | ResolvedSource = (
            LiteralSpan(raw=m.group(0), start=m.start(), end=m.end())
            if m
            else ResolvedSource(rule="jev_size_tier", inputs={"answer": tier.choice})
        )
        fields["size_tier"] = {"value": tier.choice.casefold(), "source": source}
        confidence["size_tier"] = _d(tier.confidence)

    for name in ("exit_fraction", "stop_basis", "instrument"):
        a = choice(name)
        if confident(name, a) and a.choice not in ("not_applicable", "unclear"):
            fields[name] = a.choice
    for name in (*FLAGS, "has_prose_condition", "is_correction"):
        p = noul(name).noul
        if _d(max(p, 1 - p)) < t_field:
            reasons.append(f"{name}: yes/no uncertain ({p:.2f})")
        elif name in FLAGS:
            fields[name] = p >= 0.5
        elif p >= 0.5:  # a worded condition or a correction needs the LLM to read it
            reasons.append(f"{name}: needs the slow path")

    for name in REQUIRED.get(msg_type, ()):
        if name not in fields:
            reasons.append(f"{name}: required for {msg_type} but not stated")

    link = choice("links_to")
    target: Any = link.choice
    if target not in ("new_position", "ambiguous"):
        target = next(b.id for b in ctx.provider_book if str(b.id) == target)
    if _d(link.confidence) < t_link:
        target = "ambiguous"  # C31: a weak link is a review, never a guess

    if reasons:
        return replace(base, slow_reasons=tuple(reasons))
    instrument = fields.pop("instrument", None)
    signal = ParsedSignal.model_validate(
        {
            "msg_type": msg_type,
            "instrument": instrument,
            "fields": fields,
            "link_hint": {"target": target, "confidence": _d(link.confidence)},
            "field_confidence": confidence,
        }
    )
    parsed = ParsedSignals(signals=[signal], interpreter="jev")
    if errors := verify_literal_spans(parsed, text):  # defence in depth: spans came from code
        reasons += [f"{e.field}: {e.reason}" for e in errors]
        return replace(base, slow_reasons=tuple(reasons))
    return replace(base, parsed=parsed)


def _right_span(text: str, strike: LiteralSpan | None, right: str) -> LiteralSpan | None:
    """Literal evidence for call/put: the strike token ("770c") or a word ("calls")."""
    if strike is not None and parse_raw("right", strike.raw) == right:
        return strike.model_copy()
    for m in _RIGHT_WORD.finditer(text):
        if parse_raw("right", m.group(0)) == right:
            return LiteralSpan(raw=m.group(0), start=m.start(), end=m.end())
    return None
