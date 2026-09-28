"""platform

Tables of database-schema.md §5 (F004), per-class grants, the five structural
`system_flags` rows (inactive), and FK model_calls.eval_run_id -> eval_runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28 20:54:52.635671
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT = [
    "alerts",
    "alert_acks",
    "config_changes",
    "system_flag_events",
    "manual_corrections",
    "logins",
    "eval_labels",
    "eval_runs",
]
CURRENT = [
    "config",
    "system_flags",
    "passkey_credentials",
    "enrollment_tokens",
    "auth_sessions",
    "push_subscriptions",
]
SEQUENCES = [  # identity columns
    "config_changes_id_seq",
    "system_flag_events_id_seq",
    "manual_corrections_id_seq",
    "logins_id_seq",
]
FLAGS = ["entries_blocked", "kill_switch", "catch_up", "feed_degraded", "parser_degraded"]


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=True),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('feed_silence', 'parser_degraded', 'poison', 'reconcile_mismatch', 'catch_up', 'late_correction', 'message_deleted', 'risk_limit', 'exit_floor', 'provider_disabled', 'market_data', 'invariant_block', 'note_hidden')",
            name=op.f("ck_alerts_kind"),
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'urgent')", name=op.f("ck_alerts_severity")
        ),
        sa.CheckConstraint("message_id > 0", name=op.f("ck_alerts_message_id_positive")),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_alerts")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_alerts_dedupe_key")),
    )
    op.create_index(
        "ix_alerts_created", "alerts", [sa.literal_column("created_at DESC")], unique=False
    )
    op.create_table(
        "config",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_config_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config")),
    )
    op.create_table(
        "enrollment_tokens",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_enrollment_tokens")),
    )
    op.create_table(
        "eval_runs",
        sa.Column("eval_run_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("model_ref", sa.Text(), nullable=False),
        sa.Column("split", sa.Text(), nullable=True),
        sa.Column(
            "thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('replay_gate', 'threshold_sweep', 'bench')", name=op.f("ck_eval_runs_kind")
        ),
        sa.CheckConstraint("split IN ('dev', 'test')", name=op.f("ck_eval_runs_split")),
        sa.PrimaryKeyConstraint("eval_run_id", name=op.f("pk_eval_runs")),
    )
    op.create_index(
        "ix_eval_runs_model_started",
        "eval_runs",
        ["model_ref", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_table(
        "passkey_credentials",
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "transports", sa.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("credential_id", name=op.f("pk_passkey_credentials")),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("subscription_id", name=op.f("pk_push_subscriptions")),
        sa.UniqueConstraint("endpoint", name=op.f("uq_push_subscriptions_endpoint")),
    )
    op.create_table(
        "system_flags",
        sa.Column("flag", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "flag IN ('entries_blocked', 'kill_switch', 'catch_up', 'feed_degraded', 'parser_degraded')",
            name=op.f("ck_system_flags_flag"),
        ),
        sa.PrimaryKeyConstraint("flag", name=op.f("pk_system_flags")),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("step_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["passkey_credentials.credential_id"],
            name=op.f("fk_auth_sessions_credential_id"),
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index(
        "ix_auth_sessions_live_expiry",
        "auth_sessions",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "alert_acks",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "acked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"], ["alerts.alert_id"], name=op.f("fk_alert_acks_alert_id")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["auth_sessions.session_id"], name=op.f("fk_alert_acks_session_id")
        ),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_alert_acks")),
    )
    op.create_table(
        "config_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "actor IN ('user', 'cli', 'system')", name=op.f("ck_config_changes_actor")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["auth_sessions.session_id"], name=op.f("fk_config_changes_session_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_changes")),
        sa.UniqueConstraint("version", name=op.f("uq_config_changes_version")),
    )
    op.create_table(
        "eval_labels",
        sa.Column("label_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("label", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("supersedes_label_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "labeled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('prelabel_corrected', 'manual', 'provider_correction')",
            name=op.f("ck_eval_labels_source"),
        ),
        sa.CheckConstraint("split IN ('dev', 'test')", name=op.f("ck_eval_labels_split")),
        sa.ForeignKeyConstraint(
            ["message_id", "event_type", "event_seq"],
            ["message_events.message_id", "message_events.event_type", "message_events.event_seq"],
            name=op.f("fk_eval_labels_message_id_event_type_event_seq"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["auth_sessions.session_id"], name=op.f("fk_eval_labels_session_id")
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_label_id"],
            ["eval_labels.label_id"],
            name=op.f("fk_eval_labels_supersedes_label_id"),
        ),
        sa.PrimaryKeyConstraint("label_id", name=op.f("pk_eval_labels")),
    )
    op.create_index(
        "ix_eval_labels_split_day", "eval_labels", ["split", "trading_day"], unique=False
    )
    op.create_table(
        "logins",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'step_up_success', 'step_up_failure', 'logout', 'rate_limited', 'enrolled')",
            name=op.f("ck_logins_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["auth_sessions.session_id"], name=op.f("fk_logins_session_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_logins")),
    )
    op.create_index("ix_logins_at", "logins", [sa.literal_column("at DESC")], unique=False)
    op.create_table(
        "manual_corrections",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "target_type IN ('provider_position', 'message_link', 'provider_profile')",
            name=op.f("ck_manual_corrections_target_type"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.session_id"],
            name=op.f("fk_manual_corrections_session_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manual_corrections")),
    )
    op.create_index(
        "ix_manual_corrections_target",
        "manual_corrections",
        ["target_type", "target_id"],
        unique=False,
    )
    op.create_table(
        "system_flag_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("flag", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "actor IN ('user', 'cli', 'system')", name=op.f("ck_system_flag_events_actor")
        ),
        sa.ForeignKeyConstraint(
            ["flag"], ["system_flags.flag"], name=op.f("fk_system_flag_events_flag")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.session_id"],
            name=op.f("fk_system_flag_events_session_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_flag_events")),
    )
    op.create_foreign_key(
        op.f("fk_model_calls_eval_run_id"),
        "model_calls",
        "eval_runs",
        ["eval_run_id"],
        ["eval_run_id"],
    )

    flags = sa.table("system_flags", sa.column("flag", sa.Text), sa.column("active", sa.Boolean))
    op.bulk_insert(flags, [{"flag": flag, "active": False} for flag in FLAGS])
    for table in AUDIT:
        op.execute(f"GRANT SELECT, INSERT ON {table} TO trading_app")
    for table in CURRENT:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO trading_app")
    for seq in SEQUENCES:
        op.execute(f"GRANT USAGE ON SEQUENCE {seq} TO trading_app")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_model_calls_eval_run_id"), "model_calls", type_="foreignkey")
    op.drop_table("system_flag_events")
    op.drop_index("ix_manual_corrections_target", table_name="manual_corrections")
    op.drop_table("manual_corrections")
    op.drop_index("ix_logins_at", table_name="logins")
    op.drop_table("logins")
    op.drop_index("ix_eval_labels_split_day", table_name="eval_labels")
    op.drop_table("eval_labels")
    op.drop_table("config_changes")
    op.drop_table("alert_acks")
    op.drop_index(
        "ix_auth_sessions_live_expiry",
        table_name="auth_sessions",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("auth_sessions")
    op.drop_table("system_flags")
    op.drop_table("push_subscriptions")
    op.drop_table("passkey_credentials")
    op.drop_index("ix_eval_runs_model_started", table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_table("enrollment_tokens")
    op.drop_table("config")
    op.drop_index("ix_alerts_created", table_name="alerts")
    op.drop_table("alerts")
