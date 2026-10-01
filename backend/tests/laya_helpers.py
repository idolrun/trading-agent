"""A fake laya-mlx agent: canned answers in Laya's wire format, no weights, no MLX."""

import time
from collections.abc import Mapping
from typing import Any

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from app.interpret.jev.mapping import Answer


def raw_answers(answers: Mapping[str, Answer]) -> dict[str, dict[str, Any]]:
    """Jev-shaped test answers -> what `Agent.system_one` returns. `confidence` is deliberately
    NOT the top probability (it is normalized entropy in the real model)."""
    out: dict[str, dict[str, Any]] = {}
    for qid, a in answers.items():
        if isinstance(a, ChoiceAnswer):
            out[qid] = {
                "type": "choice",
                "choice": a.choice,
                "confidence": 0.11,
                "probabilities": dict(a.probabilities),
            }
        elif isinstance(a, NoulAnswer):
            out[qid] = {"type": "noul", "noul": a.noul, "confidence": 0.11}
        elif isinstance(a, ScoreAnswer):
            out[qid] = {
                "type": "score",
                "score": a.score,
                "confidence": 0.11,
                "legend": {str(k): v for k, v in a.legend.items()},
                "probabilities": {str(k): v for k, v in a.probabilities.items()},
            }
    return out


class FakeLaya:
    def __init__(
        self, answers: Mapping[str, Answer] | None = None, *, delay_s: float = 0.0
    ) -> None:
        self.answers = raw_answers(answers or {})
        self.delay_s = delay_s
        self.calls = 0
        self.loads: list[str] = []

    def load(self, model: str) -> "FakeLaya":
        self.loads.append(model)
        return self

    def system_one(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        self.calls += 1
        time.sleep(self.delay_s)
        missing = set(questions) - set(self.answers)
        assert not missing, f"fake has no answer for {sorted(missing)}"
        return {
            "model": "fake",
            "answers": {q: self.answers[q] for q in questions},
            "usage": {"input_tokens": 1200, "output_tokens": 0},
        }
