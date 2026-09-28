"""Config validation for the model registry (TRD §4.8 rule 5, PRD C45)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.evalkit.models import EvalRun
from app.runtime_config.schema import LlmConfig

GATED_ROLES = ("extract", "note")


async def validate_llm_config(
    cfg: LlmConfig, db: AsyncSession, *, allow_ungated: bool = False
) -> list[str]:
    """Errors (empty = valid). `allow_ungated` (development only) skips the gate-run check."""
    errors: list[str] = []
    extract = cfg.roles.extract.chain
    # Vendor = provider while Bedrock is unsupported (OD-7); Claude on Bedrock must then map
    # to "anthropic", since it is not a second vendor.
    if extract and len({e.provider for e in extract}) < 2:
        errors.append("llm.roles.extract.chain needs at least two independent vendors")
    if allow_ungated:
        return errors
    entries = [(role, e) for role in GATED_ROLES for e in getattr(cfg.roles, role).chain]
    run_ids = {e.gate_run for _, e in entries if e.gate_run is not None}
    passed: dict[uuid.UUID, str] = {}
    if run_ids:
        rows = await db.execute(
            select(col(EvalRun.eval_run_id), col(EvalRun.model_ref)).where(
                col(EvalRun.eval_run_id).in_(run_ids), col(EvalRun.gate_passed).is_(True)
            )
        )
        passed = {run_id: model_ref for run_id, model_ref in rows.tuples()}
    for role, e in entries:
        if e.gate_run is None or passed.get(e.gate_run) != e.model_ref:
            errors.append(
                f"llm.roles.{role}: {e.model_ref} has no passing gate run"
                + (f" ({e.gate_run})" if e.gate_run else "")
            )
    return errors
