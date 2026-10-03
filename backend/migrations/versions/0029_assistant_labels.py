"""`eval_labels.source` + `assistant` (F082): labels an AI assistant wrote for the real corpus,
kept apart from a person's (`manual`) so metrics on them are reported as provisional.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = ["prelabel_corrected", "manual", "provider_correction"]


def _set(values: list[str]) -> None:
    op.drop_constraint(op.f("ck_eval_labels_source"), "eval_labels", type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint("source", "eval_labels", f"source IN ({listed})")


def upgrade() -> None:
    _set([*SOURCES, "assistant"])


def downgrade() -> None:
    op.execute("DELETE FROM eval_labels WHERE source = 'assistant'")  # insert-only audit: dropped
    _set(SOURCES)
