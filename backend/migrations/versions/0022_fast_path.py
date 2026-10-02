"""fast path interpreter + stage (F070)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTERPRETERS = ["jev", "llm", "none", "laya"]
STAGES = ["received", "dequeued", "context", "jev", "llm", "validated", "card", "pushed"]


def _set(table: str, column: str, values: list[str]) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{column}"), table, type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(column, table, f"{column} IN ({listed})")


def upgrade() -> None:
    _set("signals", "interpreter", [*INTERPRETERS, "fast"])
    _set("stage_timings", "stage", [*STAGES, "fast"])


def downgrade() -> None:
    op.execute("DELETE FROM stage_timings WHERE stage = 'fast'")
    _set("stage_timings", "stage", STAGES)
    # signals is append-only audit: a fast signal is relabelled 'none' (no model read it)
    op.execute("UPDATE signals SET interpreter = 'none' WHERE interpreter = 'fast'")
    _set("signals", "interpreter", INTERPRETERS)
