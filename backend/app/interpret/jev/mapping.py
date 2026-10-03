"""Jev answers → `ParsedSignals` (TRD Appendix A.3). A field is literal only when Jev picked a
code-found candidate with confidence ≥ `t_field`; its value is parsed from the candidate's exact
span. Anything Jev cannot settle is a slow-path reason, never a guess (C78, FR-10)."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal

from typesafe_sdk import Choice, ChoiceAnswer, Noul, NoulAnswer, Question, Score, ScoreAnswer

from app.interpret.candidates import Candidate, Candidates, fraction_value
from app.interpret.context import ProviderContext
from app.interpret.models import MsgType
from app.interpret.signal import (
    LiteralSpan,
    ModelSource,
    ParsedSignal,
    ParsedSignals,
    ResolvedSource,
)
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
# Number roles that no field is read from; the rest are read (SINGLE_ROLES, take_profit_level).
IRRELEVANT_ROLES = ("underlying_level", "exit_fraction", "pnl_or_percent", "date_or_time", "other")
OPTION_ENTRY_TYPES = frozenset({MsgType.ENTRY, MsgType.ADD})
STOP_TYPES = frozenset({MsgType.STOP_SET, MsgType.STOP_MOVE, MsgType.STOP_CANCEL})
EXIT_TYPES = frozenset({MsgType.TRIM, MsgType.FULL_EXIT, MsgType.SOFT_EXIT, MsgType.STOCK_EXIT})
# Flags that change what an order does only when the message carries a stop; `is_add` changes
# nothing downstream (the link decides add vs new).
STOP_FLAGS = frozenset({"candle_close", "breakeven_stop", "stop_removed"})
CONDITION_YES = Decimal("0.5")  # F079: has_prose_condition at or above: a conditional instruction
_RIGHT_WORD = re.compile(r"\b(?:calls?|puts?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class JevResult:
    msg_type: MsgType
    msg_type_confidence: Decimal
    signal_count: str
    parsed: ParsedSignals | None  # None: Jev could not settle every field (see slow_reasons)
    slow_reasons: tuple[str, ...] = ()
    # F078: doubts about which earlier position is meant (re-entry among several): review,
    # never another model (F077)
    review_reasons: tuple[str, ...] = ()
    hints: dict[str, Any] = field(default_factory=dict)  # compact answers for the slow path
    model_call_id: int | None = None


def _d(p: float) -> Decimal:
    return Decimal(str(round(p, 4)))


def _probability(p: object) -> bool:
    return isinstance(p, int | float) and math.isfinite(p) and 0 <= p <= 1


def validate_answers(
    answers: Mapping[str, Answer], questions: Mapping[str, Question]
) -> str | None:
    """F078: the response fits the questions asked, else why not (`map_answers` may then index
    freely). A Jev answer outside its options, a missing answer or a broken probability is a
    model failure (fallbacks, then review), never an exception that poisons the event."""
    for qid, q in questions.items():
        a = answers.get(qid)
        if a is None:
            return f"{qid}: no answer"
        if isinstance(q, Choice):
            if not isinstance(a, ChoiceAnswer):
                return f"{qid}: expected a choice answer"
            options = set(q.criteria)
            if a.choice not in options:
                return f"{qid}: {a.choice!r} was not offered"
            if not _probability(a.confidence):
                return f"{qid}: confidence {a.confidence!r} is not a probability"
            if any(k not in options or not _probability(v) for k, v in a.probabilities.items()):
                return f"{qid}: probabilities do not match the options"
        elif isinstance(q, Noul):
            if not isinstance(a, NoulAnswer) or not _probability(a.noul):
                return f"{qid}: expected a yes probability"
        elif isinstance(q, Score):
            if not isinstance(a, ScoreAnswer):
                return f"{qid}: expected a score answer"
            top = len(q.criteria) - 1
            if not (isinstance(a.score, int | float) and math.isfinite(a.score)):
                return f"{qid}: score {a.score!r} is not a number"
            if not 0 <= a.score <= top or not _probability(a.confidence):
                return f"{qid}: score {a.score!r} outside 0..{top}"
    return None


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
    *,
    underlying_optional: bool = False,
    interpreter: Literal["jev", "laya"] = "jev",
) -> JevResult:
    t_field, t_type, t_link = cfg.t_field, cfg.t_type, cfg.t_link
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

    def model(question: str, p: float) -> ModelSource:
        return ModelSource(interpreter=interpreter, question=question, confidence=_d(p))

    mt = choice("msg_type")
    msg_type, conf = MsgType(mt.choice), _d(mt.confidence)
    sources: dict[str, Any] = {"msg_type": model("msg_type", mt.confidence)}
    # C52: "trim or soft exit?" at a middling exit intent is a soft exit, never a sell order.
    exit_intent = answers.get("exit_intent")
    if msg_type in (MsgType.TRIM, MsgType.SOFT_EXIT) and conf < t_type:
        both = _d(mt.probabilities.get("trim", 0) + mt.probabilities.get("soft_exit", 0))
        if (
            both >= t_type
            and isinstance(exit_intent, ScoreAnswer)
            and round(exit_intent.score) == 2
        ):
            msg_type, conf = MsgType.SOFT_EXIT, both
            sources["msg_type"] = ResolvedSource(  # code's C52 rule over Jev's probabilities
                rule="c52_soft_exit", inputs={"trim_or_soft_exit": str(both)}
            )
    signal_count = choice("signal_count").choice
    base = JevResult(msg_type, conf, signal_count, parsed=None, hints=compact(answers))
    if msg_type in FEED_TYPES:  # no fields needed; routing decides on t_chat
        fields_: dict[str, Any] = {}
        ticker = choice("ticker")
        if msg_type == MsgType.WATCH and ticker.choice != "not_stated":  # F072: a watch setup
            tc = next(x for x in candidates.tickers if x.id == ticker.choice)
            if _d(ticker.confidence) >= t_field and (value := parse_raw("underlying", tc.text)):
                watched = LiteralSpan(raw=tc.text, start=tc.start, end=tc.end, candidate_id=tc.id)
                fields_["underlying"] = {"value": value, "source": watched}
        signal = ParsedSignal.model_validate(
            {"msg_type": msg_type, "fields": fields_, "field_sources": sources}
        )
        parsed = ParsedSignals(signals=[signal], interpreter=interpreter)
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
        if _d(a.confidence) < t_field:
            irrelevant = sum(a.probabilities.get(r, 0.0) for r in IRRELEVANT_ROLES)
            if _d(irrelevant) >= t_field:
                continue  # unsure which junk it is, sure it is not a number we read
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
        source: LiteralSpan | ModelSource = (
            LiteralSpan(raw=m.group(0), start=m.start(), end=m.end())
            if m
            else model("size_tier", tier.confidence)  # read from the provider's lexicon
        )
        fields["size_tier"] = {"value": tier.choice.casefold(), "source": source}
        confidence["size_tier"] = _d(tier.confidence)

    # A question that does not apply to the message ("how much is sold?" on an entry, "which
    # price is the stop?" with no stop) is answered at 0.2-0.5 by Jev: noise, not doubt. Such
    # answers are ignored instead of sending every plain entry to the slow path (replay of 518
    # real messages: 0 assembled). Where the question applies, low confidence still blocks.
    has_stop = msg_type in STOP_TYPES or "stop_level" in fields
    for name in ("exit_fraction", "stop_basis", "instrument"):
        if (name == "exit_fraction" and msg_type not in EXIT_TYPES) or (
            name == "stop_basis" and not has_stop
        ):
            continue
        a = choice(name)
        if confident(name, a) and a.choice not in ("not_applicable", "unclear"):
            fields[name] = a.choice
            sources[name] = _fraction_span(candidates, a.choice) or model(name, a.confidence)
    for name in FLAGS:
        p = noul(name).noul
        if _d(max(p, 1 - p)) >= t_field:
            fields[name] = p >= 0.5
            sources[name] = model(name, max(p, 1 - p))
        elif name in STOP_FLAGS and has_stop:
            reasons.append(f"{name}: yes/no uncertain ({p:.2f})")
    # Condition gate (F079, C30). `conditional` / `watch` messages are setups (no card). On an
    # instruction, a worded condition changes the trade: a missed one turns "if it breaks 770,
    # calls" into an unconditional order, so a likely condition (p >= 0.5) and a doubtful one
    # (p in [1 - t_field, 0.5)) both go to the semantic fallback (LLM, else a person). Below
    # 1 - t_field Jev is sure there is none. Corrections need no doubt band: they also arrive as
    # edits (F038) and the user sees the card they would change.
    condition = _d(noul("has_prose_condition").noul)
    if msg_type != MsgType.CONDITIONAL:
        if condition >= CONDITION_YES:
            reasons.append(f"conditional_instruction: a worded condition ({condition:.2f})")
        elif condition >= 1 - t_field:
            reasons.append(f"condition_doubt: maybe a worded condition ({condition:.2f})")
    if noul("is_correction").noul >= 0.5:
        reasons.append("is_correction: needs the slow path")

    missing = [
        name
        for name in REQUIRED.get(msg_type, ())
        if name not in fields
        # validation makes the "confirm the underlying" review card (FR-14)
        and not (name == "underlying" and underlying_optional and msg_type in OPTION_ENTRY_TYPES)
    ]
    reasons += [f"{name}: required for {msg_type} but not stated" for name in missing]
    review: tuple[str, ...] = ()
    if missing and ctx.reentry_candidates:  # F078: "back in" with no contract of its own
        n = len(ctx.reentry_candidates)
        review = (f"reentry: {n} earlier position(s) could be meant, the message names none",)

    link = choice("links_to")
    target: Any = link.choice
    if target not in ("new_position", "ambiguous"):  # an id outside the candidates: no link
        target = next((b.id for b in ctx.link_candidates if str(b.id) == target), "ambiguous")
    if _d(link.confidence) < t_link:
        target = "ambiguous"  # C31: a weak link is a review, never a guess

    if reasons:
        return replace(base, slow_reasons=tuple(reasons), review_reasons=review)
    instrument = fields.pop("instrument", None)
    signal = ParsedSignal.model_validate(
        {
            "msg_type": msg_type,
            "instrument": instrument,
            "fields": fields,
            "link_hint": {"target": target, "confidence": _d(link.confidence)},
            "field_confidence": confidence,
            "field_sources": sources,
        }
    )
    parsed = ParsedSignals(signals=[signal], interpreter=interpreter)
    if errors := verify_literal_spans(parsed, text):  # defence in depth: spans came from code
        reasons += [f"{e.field}: {e.reason}" for e in errors]
        return replace(base, slow_reasons=tuple(reasons))
    return replace(base, parsed=parsed)


def _fraction_span(candidates: Candidates, value: str) -> LiteralSpan | None:
    """An exit fraction the text itself says ("half", "1/2", "50%"): literal, not a judgment."""
    for c in candidates.fraction_words:
        if fraction_value(c.text) == value:
            return LiteralSpan(raw=c.text, start=c.start, end=c.end, candidate_id=c.id)
    return None


def _right_span(text: str, strike: LiteralSpan | None, right: str) -> LiteralSpan | None:
    """Literal evidence for call/put: the strike token ("770c") or a word ("calls")."""
    if strike is not None and parse_raw("right", strike.raw) == right:
        return strike.model_copy()
    for m in _RIGHT_WORD.finditer(text):
        if parse_raw("right", m.group(0)) == right:
            return LiteralSpan(raw=m.group(0), start=m.start(), end=m.end())
    return None
