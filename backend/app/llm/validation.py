"""Config validation for the model registry (TRD §4.8 rule 5, PRD C45)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.evalkit.models import EvalRun, EvalRunKind, Split
from app.llm.params import param_errors
from app.runtime_config.schema import ChainEntry, LlmConfig

GATED_ROLES = ("extract", "note")
# Every role the app calls; each call is billed, so each model needs a price (TRD §4.8 rule 8).
PRICED_ROLES = ("extract", "note", "prelabel")


def gate_run_error(run: EvalRun | None, entry: ChainEntry) -> str | None:
    """None = `run` is a passing replay gate of `entry` on the test split (evalkit/gate.py)."""
    if run is None:
        return "not found"
    if run.kind != EvalRunKind.REPLAY_GATE:
        return f"kind is {run.kind}, not {EvalRunKind.REPLAY_GATE}"
    if run.split != Split.TEST:
        return f"split is {run.split}, not {Split.TEST}"
    if run.model_ref != entry.model_ref:
        return f"model_ref is {run.model_ref}"
    if run.gate_passed is not True:
        return "gate did not pass"
    checks = run.metrics.get("checks")
    if not isinstance(checks, list) or not checks:
        return "no gate checks recorded"
    if not all(isinstance(c, dict) and c.get("passed") is True for c in checks):
        return "a gate check failed"
    return None


async def validate_llm_config(
    cfg: LlmConfig, db: AsyncSession, *, allow_ungated: bool = False
) -> list[str]:
    """Errors (empty = valid). `allow_ungated` (development only) skips the gate-run and
    price checks."""
    errors: list[str] = []
    for model_key, params in cfg.params.items():
        errors += param_errors(model_key, params)
    extract = cfg.roles.extract.chain
    # Vendor = provider while Bedrock is unsupported (OD-7); Claude on Bedrock must then map
    # to "anthropic", since it is not a second vendor.
    if extract and len({e.provider for e in extract}) < 2:
        errors.append("llm.roles.extract.chain needs at least two independent vendors")
    if allow_ungated:
        return errors
    for role in PRICED_ROLES:
        for e in getattr(cfg.roles, role).chain:
            if e.model_key not in cfg.prices:
                errors.append(f"llm.roles.{role}: {e.model_key} has no entry in llm.prices")
    entries = [(role, e) for role in GATED_ROLES for e in getattr(cfg.roles, role).chain]
    run_ids = {e.gate_run for _, e in entries if e.gate_run is not None}
    runs: dict[uuid.UUID, EvalRun] = {}
    if run_ids:
        rows = await db.scalars(select(EvalRun).where(col(EvalRun.eval_run_id).in_(run_ids)))
        runs = {r.eval_run_id: r for r in rows.all()}
    for role, e in entries:
        if e.gate_run is None:
            errors.append(f"llm.roles.{role}: {e.model_ref} has no passing gate run")
        elif (why := gate_run_error(runs.get(e.gate_run), e)) is not None:
            errors.append(
                f"llm.roles.{role}: {e.model_ref} has no passing gate run ({e.gate_run}: {why})"
            )
    return errors
