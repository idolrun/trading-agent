"""Laya: `busy` call outcome and its own stage (F074)

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOMES = [
    "ok",
    "timeout",
    "rate_limited",
    "server_error",
    "connection_error",
    "schema_invalid",
    "empty",
    "refusal",
    "scope_blocked",
    "span_rejected",
    "circuit_open",
]
STAGES = ["received", "dequeued", "context", "jev", "llm", "validated", "card", "pushed", "fast"]


def _set(table: str, column: str, values: list[str]) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{column}"), table, type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(column, table, f"{column} IN ({listed})")


def upgrade() -> None:
    _set("model_calls", "outcome", [*OUTCOMES, "busy"])
    _set("stage_timings", "stage", [*STAGES, "laya"])


def downgrade() -> None:
    op.execute("UPDATE stage_timings SET stage = 'jev' WHERE stage = 'laya'")
    _set("stage_timings", "stage", STAGES)
    op.execute("UPDATE model_calls SET outcome = 'server_error' WHERE outcome = 'busy'")
    _set("model_calls", "outcome", OUTCOMES)
