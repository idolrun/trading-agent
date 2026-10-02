"""provider positions belong to a lane (channel, provider) (F068)

Backfills `opened_channel_id` from the opening signal's message. A position that still has no
channel (a manual add before F068 with no message) is in no lane's book; the count is printed.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE provider_positions p SET opened_channel_id = e.channel_id"
        " FROM signals s JOIN message_events e ON (e.message_id, e.event_type, e.event_seq)"
        " = (s.message_id, s.event_type, s.event_seq)"
        " WHERE p.opened_channel_id IS NULL AND s.signal_id = p.opened_by_signal_id"
    )
    left = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM provider_positions"
            " WHERE opened_channel_id IS NULL AND status <> 'closed'"
        )
    )
    if left:
        print(f"0020: {left} open provider position(s) have no channel and are in no lane's book")
    op.drop_index("ix_provider_positions_open_book", table_name="provider_positions")
    op.create_index(
        "ix_provider_positions_open_book",
        "provider_positions",
        ["provider_id", "opened_channel_id", "status"],
        postgresql_where=sa.text("status <> 'closed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_provider_positions_open_book", table_name="provider_positions")
    op.create_index(
        "ix_provider_positions_open_book",
        "provider_positions",
        ["provider_id", "status"],
        postgresql_where=sa.text("status <> 'closed'"),
    )
