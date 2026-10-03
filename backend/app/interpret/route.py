"""Deterministic router (TRD §4.4, §5.7, F078): which path a message takes after an interpreter
answered. Pure.

Why a message is not settled decides where it goes next (F078):
- `model_failure`: no usable answer (Jev off, timeout, API error, invalid answer). Another model
  may answer: Laya, then the optional LLM, then review.
- `semantic`: the model answered but is unsure or the message needs more than it can settle
  (low type confidence, several signals, a missing field, a worded condition, a correction):
  the optional LLM (a stronger reader), else review. Never Laya.
- `link`: the doubt is which earlier position is meant (re-entry among several): review. No
  model may resolve it (F077).
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.inbox.models import InterpretOutcome
from app.interpret.jev.mapping import FEED_TYPES, JevResult
from app.interpret.signal import ParsedSignals
from app.runtime_config.schema import RuntimeConfig

Kind = Literal["model_failure", "semantic", "link"]
__all__ = ["InterpretOutcome", "Kind", "RouteDecision", "route"]


@dataclass(frozen=True)
class RouteDecision:
    path: Literal["feed_only", "assemble", "slow_path"]
    reasons: tuple[str, ...] = ()
    parsed: ParsedSignals | None = None  # set for feed_only / assemble
    hints: dict[str, Any] | None = field(default=None)  # Jev answers for the slow path
    link_ambiguous: bool = False  # assemble with an ambiguous link → review card (F021)
    kind: Kind | None = None  # slow_path only: why it was not settled


def route(result: JevResult | None, cfg: RuntimeConfig) -> RouteDecision:
    if result is None:  # no usable answer
        return RouteDecision("slow_path", ("jev_unavailable",), kind="model_failure")
    hints = result.hints
    if result.review_reasons:
        return RouteDecision("slow_path", result.review_reasons, hints=hints, kind="link")
    if result.msg_type in FEED_TYPES:
        if result.msg_type_confidence >= cfg.t_chat:
            return RouteDecision("feed_only", parsed=result.parsed)
        return RouteDecision("slow_path", ("msg_type: below t_chat",), hints=hints, kind="semantic")
    if result.msg_type_confidence < cfg.t_type:
        return RouteDecision("slow_path", ("msg_type: below t_type",), hints=hints, kind="semantic")
    if result.signal_count == "multiple":
        return RouteDecision("slow_path", ("signal_count: multiple",), hints=hints, kind="semantic")
    if result.slow_reasons or result.parsed is None:
        return RouteDecision("slow_path", result.slow_reasons, hints=hints, kind="semantic")
    link = result.parsed.signals[0].link_hint.target
    return RouteDecision("assemble", parsed=result.parsed, link_ambiguous=link == "ambiguous")
