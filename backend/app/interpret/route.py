"""Deterministic router (TRD §4.4, §5.7): which path a message takes after Jev. Pure."""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.interpret.jev.mapping import FEED_TYPES, JevResult
from app.interpret.signal import ParsedSignals
from app.runtime_config.schema import RuntimeConfig


@dataclass(frozen=True)
class RouteDecision:
    path: Literal["feed_only", "assemble", "slow_path"]
    reasons: tuple[str, ...] = ()
    parsed: ParsedSignals | None = None  # set for feed_only / assemble
    hints: dict[str, Any] | None = field(default=None)  # Jev answers for the slow path
    link_ambiguous: bool = False  # assemble with an ambiguous link → review card (F021)


def route(result: JevResult | None, cfg: RuntimeConfig) -> RouteDecision:
    if result is None:  # Jev disabled, unavailable or rate limited twice
        return RouteDecision("slow_path", ("jev_unavailable",))
    hints = result.hints
    if result.msg_type in FEED_TYPES:
        if result.msg_type_confidence >= cfg.t_chat:
            return RouteDecision("feed_only", parsed=result.parsed)
        return RouteDecision("slow_path", ("msg_type: below t_chat",), hints=hints)
    if result.msg_type_confidence < cfg.t_field:
        return RouteDecision("slow_path", ("msg_type: below t_field",), hints=hints)
    if result.signal_count == "multiple":
        return RouteDecision("slow_path", ("signal_count: multiple",), hints=hints)
    if result.slow_reasons or result.parsed is None:
        return RouteDecision("slow_path", result.slow_reasons, hints=hints)
    link = result.parsed.signals[0].link_hint.target
    return RouteDecision("assemble", parsed=result.parsed, link_ambiguous=link == "ambiguous")
