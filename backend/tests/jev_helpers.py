"""Recorded-shape Jev answers for tests (SDK answer models, no live calls)."""

from decimal import Decimal

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from app.interpret.jev.mapping import Answer
from app.runtime_config.schema import RuntimeConfig

EXAMPLE = "spy 770c .95 lotto, sl 769 spy"  # TRD §4.2
CFG = RuntimeConfig(
    option_tiers={"default": Decimal(500), "small": Decimal(250), "lotto": Decimal(100)}
)


def c(choice: str, confidence: float = 0.95, **others: float) -> ChoiceAnswer:
    probabilities = {choice: confidence, **others}
    return ChoiceAnswer(
        type="choice", choice=choice, confidence=confidence, probabilities=probabilities
    )


def n(p: float) -> NoulAnswer:
    return NoulAnswer(type="noul", noul=p)


def score(value: float) -> ScoreAnswer:
    return ScoreAnswer(
        type="score",
        score=value,
        confidence=1.0,
        legend={i: f"level {i}" for i in range(5)},
        probabilities={round(value): 1.0},
    )


def example_answers(**overrides: Answer) -> dict[str, Answer]:
    """Confident answers for the TRD §4.2 example; override per test."""
    answers: dict[str, Answer] = {
        "msg_type": c("entry"),
        "signal_count": c("one"),
        "instrument": c("option"),
        "right": c("call"),
        "exit_fraction": c("not_stated", 0.9),
        "stop_basis": c("underlying", 0.9),
        "candle_close": n(0.05),
        "has_prose_condition": n(0.05),
        "is_correction": n(0.02),
        "breakeven_stop": n(0.02),
        "stop_removed": n(0.02),
        "exit_intent": score(0),
        "ticker": c("t1", 0.93),
        "expiry": c("not_stated", 0.9),
        "size_tier": c("lotto", 0.9),
        "links_to": c("new_position"),
        "role_n1": c("strike", 0.97),
        "role_n2": c("entry_price"),
        "role_n3": c("stop_level", 0.9),
    }
    return {**answers, **overrides}
