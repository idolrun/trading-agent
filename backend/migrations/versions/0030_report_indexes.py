"""Indexes for the trading reports (F083): fills and closed lots are windowed by time.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_fills_filled_at", "fills", ["filled_at"])
    op.create_index(
        "ix_lots_closed_at",
        "lots",
        ["closed_at"],
        postgresql_where=sa.text("closed_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_lots_closed_at", table_name="lots")
    op.drop_index("ix_fills_filled_at", table_name="fills")
