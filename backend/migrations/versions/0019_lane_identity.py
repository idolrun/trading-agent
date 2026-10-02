"""Discord event identity: guild + thread, and the lane key on the inbox (F067)

`message_events.channel_id` stays the context channel (a thread message carries its parent's
id); `thread_id` / `guild_id` are new. `inbox.channel_id` makes (channel, provider) the
sequencing unit (F068).

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("message_events", sa.Column("thread_id", sa.BigInteger(), nullable=True))
    op.add_column("message_events", sa.Column("guild_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint("thread_id_positive", "message_events", "thread_id > 0")
    op.create_check_constraint("guild_id_positive", "message_events", "guild_id > 0")
    op.create_index(
        "ix_message_events_lane_posted",
        "message_events",
        ["channel_id", "provider_id", sa.text("posted_at DESC")],
    )
    op.create_index("ix_message_events_thread_id", "message_events", ["thread_id"])

    op.add_column("inbox", sa.Column("channel_id", sa.BigInteger(), nullable=True))
    op.execute(
        "UPDATE inbox i SET channel_id = e.channel_id FROM message_events e"
        " WHERE (e.message_id, e.event_type, e.event_seq)"
        " = (i.message_id, i.event_type, i.event_seq)"
    )
    op.alter_column("inbox", "channel_id", nullable=False)
    op.drop_index("ix_inbox_next_for_provider", table_name="inbox")
    op.create_index(
        "ix_inbox_next_for_lane",
        "inbox",
        ["provider_id", "channel_id", "posted_at", "message_id"],
        postgresql_where=sa.text("status IN ('received', 'processing')"),
    )
    op.drop_index("ix_inbox_applied", table_name="inbox")
    op.create_index(
        "ix_inbox_applied",
        "inbox",
        ["provider_id", "channel_id", sa.text("posted_at DESC")],
        postgresql_where=sa.text("status IN ('done', 'stale')"),
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_applied", table_name="inbox")
    op.create_index(
        "ix_inbox_applied",
        "inbox",
        ["provider_id", sa.text("posted_at DESC")],
        postgresql_where=sa.text("status IN ('done', 'stale')"),
    )
    op.drop_index("ix_inbox_next_for_lane", table_name="inbox")
    op.create_index(
        "ix_inbox_next_for_provider",
        "inbox",
        ["provider_id", "posted_at", "message_id"],
        postgresql_where=sa.text("status IN ('received', 'processing')"),
    )
    op.drop_column("inbox", "channel_id")
    op.drop_index("ix_message_events_thread_id", table_name="message_events")
    op.drop_index("ix_message_events_lane_posted", table_name="message_events")
    op.drop_column("message_events", "guild_id")
    op.drop_column("message_events", "thread_id")
