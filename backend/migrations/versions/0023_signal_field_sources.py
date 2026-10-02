"""signals.field_sources: provenance of the plain fields (F071)

msg_type, instrument, exit_fraction, stop_basis and the flags record whether a literal span,
a code rule (`resolved`) or a model's judgment (`model`) set them.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column("field_sources", JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("signals", "field_sources")
