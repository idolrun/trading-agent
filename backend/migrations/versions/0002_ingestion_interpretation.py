"""ingestion interpretation

Tables of database-schema.md §3 (F003) with per-class grants for trading_app (§1.1).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 20:51:25.721851
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT = ["message_events", "stage_timings", "model_calls", "signals", "message_links"]
CURRENT = ["inbox", "dispatcher_heartbeat"]
SEQUENCES = ["model_calls_id_seq", "message_links_id_seq"]  # identity columns


def upgrade() -> None:
    op.create_table(
        "dispatcher_heartbeat",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_dispatcher_heartbeat_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dispatcher_heartbeat")),
    )
    op.create_table(
        "message_events",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_to_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "mentioned_author_ids",
            sa.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("backfilled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type = 'delete' OR text IS NOT NULL",
            name=op.f("ck_message_events_text_unless_delete"),
        ),
        sa.CheckConstraint(
            "event_type IN ('create', 'edit', 'delete')", name=op.f("ck_message_events_event_type")
        ),
        sa.CheckConstraint("channel_id > 0", name=op.f("ck_message_events_channel_id_positive")),
        sa.CheckConstraint("event_seq >= 0", name=op.f("ck_message_events_event_seq_non_negative")),
        sa.CheckConstraint("message_id > 0", name=op.f("ck_message_events_message_id_positive")),
        sa.CheckConstraint("provider_id > 0", name=op.f("ck_message_events_provider_id_positive")),
        sa.CheckConstraint("reply_to_id > 0", name=op.f("ck_message_events_reply_to_id_positive")),
        sa.PrimaryKeyConstraint(
            "message_id", "event_type", "event_seq", name=op.f("pk_message_events")
        ),
    )
    op.create_index(
        "ix_message_events_channel_message",
        "message_events",
        ["channel_id", sa.literal_column("message_id DESC")],
        unique=False,
    )
    op.create_index("ix_message_events_posted_at", "message_events", ["posted_at"], unique=False)
    op.create_index(
        "ix_message_events_provider_posted",
        "message_events",
        ["provider_id", sa.literal_column("posted_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_message_events_reply_to_id", "message_events", ["reply_to_id"], unique=False
    )
    op.create_table(
        "inbox",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("late", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("backfilled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "skip_reason IN ('muted_author', 'channel_not_configured')",
            name=op.f("ck_inbox_skip_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('received', 'processing', 'done', 'failed', 'stale')",
            name=op.f("ck_inbox_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_inbox_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["message_id", "event_type", "event_seq"],
            ["message_events.message_id", "message_events.event_type", "message_events.event_seq"],
            name=op.f("fk_inbox_message_id_event_type_event_seq"),
        ),
        sa.PrimaryKeyConstraint("message_id", "event_type", "event_seq", name=op.f("pk_inbox")),
    )
    op.create_index(
        "ix_inbox_applied",
        "inbox",
        ["provider_id", sa.literal_column("posted_at DESC")],
        unique=False,
        postgresql_where=sa.text("status IN ('done', 'stale')"),
    )
    op.create_index(
        "ix_inbox_backlog",
        "inbox",
        ["posted_at"],
        unique=False,
        postgresql_where=sa.text("status = 'received'"),
    )
    op.create_index(
        "ix_inbox_next_for_provider",
        "inbox",
        ["provider_id", "posted_at", "message_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('received', 'processing')"),
    )
    op.create_table(
        "model_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("event_seq", sa.Integer(), nullable=True),
        sa.Column("card_id", sa.Uuid(), nullable=True),
        sa.Column("eval_run_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("tokens_cached", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ok', 'timeout', 'rate_limited', 'server_error', 'connection_error', 'schema_invalid', 'empty', 'refusal', 'scope_blocked', 'span_rejected', 'circuit_open')",
            name=op.f("ck_model_calls_outcome"),
        ),
        sa.CheckConstraint(
            "provider IN ('typesafe', 'anthropic', 'openai', 'deepseek', 'openai_compat', 'bedrock')",
            name=op.f("ck_model_calls_provider"),
        ),
        sa.CheckConstraint(
            "role IN ('interpret', 'extract', 'note', 'prelabel')", name=op.f("ck_model_calls_role")
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_model_calls_attempt_positive")),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_model_calls_latency_non_negative")),
        sa.ForeignKeyConstraint(
            ["message_id", "event_type", "event_seq"],
            ["message_events.message_id", "message_events.event_type", "message_events.event_seq"],
            name=op.f("fk_model_calls_message_id_event_type_event_seq"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_calls")),
    )
    op.create_index(
        "ix_model_calls_message",
        "model_calls",
        ["message_id", "event_type", "event_seq"],
        unique=False,
    )
    op.create_index(
        "ix_model_calls_provider_model_started",
        "model_calls",
        ["provider", "model", "started_at"],
        unique=False,
    )
    op.create_index("ix_model_calls_started_at", "model_calls", ["started_at"], unique=False)
    op.create_table(
        "stage_timings",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "stage IN ('received', 'dequeued', 'context', 'jev', 'llm', 'validated', 'card', 'pushed')",
            name=op.f("ck_stage_timings_stage"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "event_type", "event_seq"],
            ["message_events.message_id", "message_events.event_type", "message_events.event_seq"],
            name=op.f("fk_stage_timings_message_id_event_type_event_seq"),
        ),
        sa.PrimaryKeyConstraint(
            "message_id",
            "event_type",
            "event_seq",
            "attempt",
            "stage",
            name=op.f("pk_stage_timings"),
        ),
    )
    op.create_index("ix_stage_timings_stage_at", "stage_timings", ["stage", "at"], unique=False)
    op.create_table(
        "signals",
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_type", sa.Text(), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=True),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("interpreter", sa.Text(), nullable=False),
        sa.Column("model_call_id", sa.BigInteger(), nullable=True),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "instrument IN ('option', 'stock', 'unclear')", name=op.f("ck_signals_instrument")
        ),
        sa.CheckConstraint(
            "interpreter IN ('jev', 'llm', 'none')", name=op.f("ck_signals_interpreter")
        ),
        sa.CheckConstraint(
            "msg_type IN ('entry', 'add', 'trim', 'full_exit', 'soft_exit', 'stop_set', 'stop_move', 'stop_cancel', 'tp_set', 'tp_hit_notice', 'correction', 'watch', 'conditional', 'commentary_pnl', 'stock_entry', 'stock_exit', 'out_of_scope_strategy', 'chatter', 'unparsed')",
            name=op.f("ck_signals_msg_type"),
        ),
        sa.CheckConstraint("idx >= 0", name=op.f("ck_signals_idx_non_negative")),
        sa.CheckConstraint("provider_id > 0", name=op.f("ck_signals_provider_id_positive")),
        sa.ForeignKeyConstraint(
            ["message_id", "event_type", "event_seq"],
            ["message_events.message_id", "message_events.event_type", "message_events.event_seq"],
            name=op.f("fk_signals_message_id_event_type_event_seq"),
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"], ["model_calls.id"], name=op.f("fk_signals_model_call_id")
        ),
        sa.PrimaryKeyConstraint("signal_id", name=op.f("pk_signals")),
        sa.UniqueConstraint(
            "message_id",
            "event_type",
            "event_seq",
            "idx",
            name=op.f("uq_signals_message_id_event_type_event_seq_idx"),
        ),
        sa.UniqueConstraint(
            "signal_id", "provider_id", name=op.f("uq_signals_signal_id_provider_id")
        ),
    )
    op.create_index(
        "ix_signals_provider_created",
        "signals",
        ["provider_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "message_links",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_position_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "candidate_position_ids",
            sa.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("supersedes_link_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(outcome IN ('linked', 'new_position')) = (provider_position_id IS NOT NULL)",
            name=op.f("ck_message_links_position_iff_linked"),
        ),
        sa.CheckConstraint(
            "method IN ('reply', 'jev', 'llm', 'user_correction', 'code')",
            name=op.f("ck_message_links_method"),
        ),
        sa.CheckConstraint(
            "outcome IN ('linked', 'new_position', 'none', 'ambiguous')",
            name=op.f("ck_message_links_outcome"),
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_message_links_confidence_range")
        ),
        sa.ForeignKeyConstraint(
            ["signal_id", "provider_id"],
            ["signals.signal_id", "signals.provider_id"],
            name=op.f("fk_message_links_signal_id_provider_id"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_link_id"],
            ["message_links.id"],
            name=op.f("fk_message_links_supersedes_link_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_links")),
    )
    op.create_index(
        "ix_message_links_provider_position_id",
        "message_links",
        ["provider_position_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_links_signal_latest",
        "message_links",
        ["signal_id", sa.literal_column("id DESC")],
        unique=False,
    )

    for table in AUDIT:
        op.execute(f"GRANT SELECT, INSERT ON {table} TO trading_app")
    for table in CURRENT:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO trading_app")
    for seq in SEQUENCES:
        op.execute(f"GRANT USAGE ON SEQUENCE {seq} TO trading_app")


def downgrade() -> None:
    op.drop_index("ix_message_links_signal_latest", table_name="message_links")
    op.drop_index("ix_message_links_provider_position_id", table_name="message_links")
    op.drop_table("message_links")
    op.drop_index("ix_signals_provider_created", table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_stage_timings_stage_at", table_name="stage_timings")
    op.drop_table("stage_timings")
    op.drop_index("ix_model_calls_started_at", table_name="model_calls")
    op.drop_index("ix_model_calls_provider_model_started", table_name="model_calls")
    op.drop_index("ix_model_calls_message", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index(
        "ix_inbox_next_for_provider",
        table_name="inbox",
        postgresql_where=sa.text("status IN ('received', 'processing')"),
    )
    op.drop_index(
        "ix_inbox_backlog", table_name="inbox", postgresql_where=sa.text("status = 'received'")
    )
    op.drop_index(
        "ix_inbox_applied",
        table_name="inbox",
        postgresql_where=sa.text("status IN ('done', 'stale')"),
    )
    op.drop_table("inbox")
    op.drop_index("ix_message_events_reply_to_id", table_name="message_events")
    op.drop_index("ix_message_events_provider_posted", table_name="message_events")
    op.drop_index("ix_message_events_posted_at", table_name="message_events")
    op.drop_index("ix_message_events_channel_message", table_name="message_events")
    op.drop_table("message_events")
    op.drop_table("dispatcher_heartbeat")
