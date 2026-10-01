"""Jev questions -> Laya question dicts, and Laya answers -> the SDK answer models that
`map_answers` reads."""

import json
from collections.abc import Mapping
from typing import Any

from typesafe_sdk import Choice, ChoiceAnswer, Noul, NoulAnswer, Question, Score, ScoreAnswer

from app.interpret.jev.mapping import Answer


def to_laya_questions(questions: Mapping[str, Question]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for qid, q in questions.items():
        if not isinstance(q, Choice | Noul | Score):  # the SDK's wire-model variants
            raise TypeError(f"question {qid!r} is not a Choice, Noul or Score")
        out[qid] = q.model_dump(mode="json")
    return out


def to_answers(raw: Mapping[str, Mapping[str, Any]]) -> dict[str, Answer]:
    """Laya's `confidence` is normalized entropy (1 - H/log k), a different scale from the top
    probability that `map_answers` gates on (0.9 vs 0.5 for a 90/10 yes-no split), so choice and
    score confidence are replaced by the top probability."""
    out: dict[str, Answer] = {}
    for qid, a in raw.items():
        kind = a["type"]
        if kind == "choice":
            probs = a["probabilities"]
            data = {
                "type": "choice",
                "choice": a["choice"],
                "confidence": probs[a["choice"]],
                "probabilities": probs,
            }
            out[qid] = ChoiceAnswer.model_validate_json(json.dumps(data))
        elif kind == "noul":
            out[qid] = NoulAnswer.model_validate_json(
                json.dumps({"type": "noul", "noul": a["noul"]})
            )
        else:
            probs = a["probabilities"]
            data = {
                "type": "score",
                "score": a["score"],
                "confidence": max(probs.values()),
                "legend": a["legend"],
                "probabilities": probs,
            }
            out[qid] = ScoreAnswer.model_validate_json(json.dumps(data))
    return out
