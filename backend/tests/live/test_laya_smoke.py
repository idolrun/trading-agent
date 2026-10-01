"""Real laya-mlx weights on Apple Silicon (downloads ~0.85 GB on first run). Skipped by default:

    uv run pytest -m live tests/live/test_laya_smoke.py -s

Checks the wiring against the real model, not its accuracy (see docs/reviews/laya-evaluation-*)."""

import platform
import time

import pytest

from app.interpret.candidates import find_candidates
from app.interpret.jev.questions import build_questions
from app.interpret.laya.client import load_agent
from app.interpret.laya.convert import to_answers, to_laya_questions
from app.interpret.laya.state import build_laya_state, trim_context
from tests.jev_helpers import CFG, EXAMPLE
from tests.llm_helpers import ctx

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(platform.machine() != "arm64", reason="MLX needs Apple Silicon"),
]


def test_the_real_model_answers_every_jev_question() -> None:
    agent = load_agent("aac6fef/laya-mlx")
    context = trim_context(ctx(EXAMPLE))
    candidates = find_candidates(EXAMPLE)
    questions = build_questions(context, candidates, sorted(CFG.option_tiers or {}))
    started = time.monotonic()
    out = agent.system_one(build_laya_state(context), to_laya_questions(questions))
    answers = to_answers(out["answers"])
    print(f"{len(questions)} questions in {time.monotonic() - started:.1f}s")
    assert set(answers) == set(questions)
