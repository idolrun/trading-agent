"""The lane-head index covers next_for_lane's ORDER BY: live before backfilled (F089).

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WHERE = sa.text("status IN ('received', 'processing')")


def upgrade() -> None:
    op.drop_index("ix_inbox_next_for_lane", table_name="inbox")
    op.create_index(
        "ix_inbox_next_for_lane",
        "inbox",
        ["provider_id", "channel_id", "backfilled", "posted_at", "message_id"],
        postgresql_where=WHERE,
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_next_for_lane", table_name="inbox")
    op.create_index(
        "ix_inbox_next_for_lane",
        "inbox",
        ["provider_id", "channel_id", "posted_at", "message_id"],
        postgresql_where=WHERE,
    )
