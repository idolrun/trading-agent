"""The replay gate and the threshold sweep (PRD §17 stage 1, C66, C67; TRD §4.8 rule 5, §12).

Gate: replay the test split with the slow-path chain pinned to one `provider:model:prompt`,
check the stage-1 criteria, and record one `eval_runs` row. Its id is what a registry chain
entry names as `gate_run`. Each check is logged; three failed gate runs in a row on the same
test set mean the set has been overfit: refresh it (label new trading days).
Sweep: `t_chat`/`t_field` over the dev split only, never the test split.
"""

import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from itertools import product
from typing import Any

from sqlalchemy import select
from sqlmodel import col

from app.db import transaction
from app.evalkit.labels import latest_labels
from app.evalkit.models import EvalRun, EvalRunKind, Split
from app.evalkit.replay import measure, parse_model_ref, pin_extract
from app.graph.message_graph import GraphDeps
from app.runtime_config.schema import RuntimeConfig

logger = logging.getLogger(__name__)

MIN_ACTIONABLE = 500
MIN_NON_ACTIONABLE = 2000
MIN_PROVIDERS = 5
MIN_RECALL = 0.98
REFRESH_AFTER = 3  # consecutive failed gate runs on one test set (C67)
CHAT_GRID = (Decimal("0.8"), Decimal("0.9"), Decimal("0.95"))
FIELD_GRID = (Decimal("0.7"), Decimal("0.8"), Decimal("0.9"))


@dataclass(frozen=True)
class Check:
    name: str
    value: float | int | None
    need: str
    passed: bool


@dataclass(frozen=True)
class GateResult:
    run_id: uuid.UUID
    passed: bool
    checks: list[Check]
    refresh_test_set: bool


def _at_least(name: str, value: float | int | None, need: float) -> Check:
    return Check(name, value, f">= {need}", value is not None and value >= need)


def gate_checks(m: dict[str, Any]) -> list[Check]:
    """PRD §17 stage 1: dataset size and provider count, then the three quality criteria."""
    return [
        _at_least("actionable_messages", m["actionable"], MIN_ACTIONABLE),
        _at_least("non_actionable_messages", m["non_actionable"], MIN_NON_ACTIONABLE),
        _at_least("providers", m["providers"], MIN_PROVIDERS),
        Check("normal_card_field_accuracy", m["field_accuracy"], "== 1", m["field_accuracy"] == 1),
        Check("wrong_contracts", m["wrong_contracts"], "== 0", m["wrong_contracts"] == 0),
        _at_least("actionable_recall", m["actionable_recall"], MIN_RECALL),
    ]


def _thresholds(cfg: RuntimeConfig) -> dict[str, Any]:
    return {k: float(getattr(cfg, k)) for k in ("t_chat", "t_field", "t_link")}


async def run_gate(deps: GraphDeps, model_ref: str) -> GateResult:
    cfg = pin_extract(deps.cfg(), parse_model_ref(model_ref))
    started = deps.clock.now()
    async with deps.sessions() as db:
        events = await latest_labels(db)
    days = ",".join(sorted({e.day.isoformat() for e in events if e.split == Split.TEST}))
    test_set = hashlib.sha256(days.encode()).hexdigest()[:16]
    metrics = await measure(deps, events, Split.TEST, cfg)
    checks = gate_checks(metrics)
    for c in checks:  # C67: every check of the test set is logged
        logger.info(
            "gate check",
            extra={"model_ref": model_ref, "check": c.name, "value": c.value, "passed": c.passed},
        )
    passed = all(c.passed for c in checks)
    run = EvalRun(
        kind=EvalRunKind.REPLAY_GATE,
        model_ref=model_ref,
        split=Split.TEST,
        thresholds=_thresholds(cfg),
        metrics={**metrics, "checks": [asdict(c) for c in checks], "test_set": test_set},
        gate_passed=passed,
        started_at=started,
        finished_at=deps.clock.now(),
    )
    async with deps.sessions() as db, transaction(db):
        db.add(run)
        await db.flush()
        recent = (
            await db.scalars(
                select(EvalRun)
                .where(
                    col(EvalRun.kind) == EvalRunKind.REPLAY_GATE,
                    col(EvalRun.split) == Split.TEST,
                )
                .order_by(col(EvalRun.started_at).desc())
                .limit(REFRESH_AFTER)
            )
        ).all()
    refresh = len(recent) == REFRESH_AFTER and all(
        not r.gate_passed and r.metrics.get("test_set") == test_set for r in recent
    )
    return GateResult(run.eval_run_id, passed, checks, refresh)


async def run_sweep(deps: GraphDeps, model_ref: str) -> EvalRun:
    """Replay the dev split at every `t_chat` x `t_field` point. The best point is the cheapest
    (lowest slow-path share) that keeps recall >= 98% with no wrong contract. `t_link` is not
    swept: it acts at correlation (F021), after the interpretation a replay stops at."""
    base = pin_extract(deps.cfg(), parse_model_ref(model_ref))
    started = deps.clock.now()
    async with deps.sessions() as db:
        events = await latest_labels(db)
    points: list[dict[str, Any]] = []
    for t_chat, t_field in product(CHAT_GRID, FIELD_GRID):
        cfg = base.model_copy(update={"t_chat": t_chat, "t_field": t_field})
        m = await measure(deps, events, Split.DEV, cfg)
        points.append(
            {"t_chat": float(t_chat), "t_field": float(t_field)}
            | {k: v for k, v in m.items() if k != "confusion"}
        )
    feasible = [
        p
        for p in points
        if p["wrong_contracts"] == 0 and (p["actionable_recall"] or 0) >= MIN_RECALL
    ]
    best = min(feasible, key=lambda p: p["slow_path_share"] or 0, default=None)
    run = EvalRun(
        kind=EvalRunKind.THRESHOLD_SWEEP,
        model_ref=model_ref,
        split=Split.DEV,
        thresholds={**_thresholds(base), "best": None if best is None else _pair(best)},
        metrics={"points": points},
        started_at=started,
        finished_at=deps.clock.now(),
    )
    async with deps.sessions() as db, transaction(db):
        db.add(run)
    return run


def _pair(point: dict[str, Any]) -> dict[str, float]:
    return {"t_chat": point["t_chat"], "t_field": point["t_field"]}
