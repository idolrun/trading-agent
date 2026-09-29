"""password auth

F009 revised: passkeys are replaced by email + password. Adds `users` (allowlist) and the
append-only `audit_log`; sessions now belong to a user. Passkey tables and `logins` are
dropped. Existing sessions are revoked (not deleted: `approvals.session_id` references them).
One-way: the downgrade restores the old shape only, not the data.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = [
    "user_created",
    "user_deactivated",
    "user_reactivated",
    "password_reset",
    "password_changed",
    "login_success",
    "login_failed",
    "logout",
    "step_up_success",
    "step_up_failure",
    "rate_limited",
    "trade_approved",
    "trade_rejected",
    "card_edited",
    "order_created",
    "order_cancelled",
    "lot_adjusted",
    "kill_switch",
]
OLD_OUTCOMES = "'success', 'failure', 'step_up_success', 'step_up_failure', 'logout', 'rate_limited', 'enrolled'"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("user_email", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN (" + ", ".join(f"'{a}'" for a in ACTIONS) + ")",
            name=op.f("ck_audit_log_action"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_at", "audit_log", [sa.literal_column("at DESC")], unique=False)
    op.create_index(
        "ix_audit_log_user_at",
        "audit_log",
        ["user_id", sa.literal_column("at DESC")],
        unique=False,
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON users TO trading_app")
    op.execute("GRANT SELECT, INSERT ON audit_log TO trading_app")  # append-only
    op.execute("GRANT USAGE ON SEQUENCE audit_log_id_seq TO trading_app")

    op.execute("UPDATE auth_sessions SET revoked_at = now() WHERE revoked_at IS NULL")
    op.drop_constraint(op.f("fk_auth_sessions_credential_id"), "auth_sessions", type_="foreignkey")
    op.drop_column("auth_sessions", "credential_id")
    op.add_column("auth_sessions", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_auth_sessions_user_id"), "auth_sessions", "users", ["user_id"], ["user_id"]
    )
    op.drop_table("logins")
    op.drop_table("enrollment_tokens")
    op.drop_table("passkey_credentials")


def downgrade() -> None:
    op.create_table(
        "passkey_credentials",
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "transports",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
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
    op.execute("DELETE FROM auth_sessions WHERE user_id IS NOT NULL")
    op.drop_constraint(op.f("fk_auth_sessions_user_id"), "auth_sessions", type_="foreignkey")
    op.drop_column("auth_sessions", "user_id")
    op.execute("DELETE FROM auth_sessions")
    op.add_column("auth_sessions", sa.Column("credential_id", sa.LargeBinary(), nullable=False))
    op.create_foreign_key(
        op.f("fk_auth_sessions_credential_id"),
        "auth_sessions",
        "passkey_credentials",
        ["credential_id"],
        ["credential_id"],
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
        sa.CheckConstraint(f"outcome IN ({OLD_OUTCOMES})", name=op.f("ck_logins_outcome")),
        sa.ForeignKeyConstraint(
            ["session_id"], ["auth_sessions.session_id"], name=op.f("fk_logins_session_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_logins")),
    )
    op.create_index("ix_logins_at", "logins", [sa.literal_column("at DESC")], unique=False)
    op.execute("GRANT SELECT, INSERT ON logins TO trading_app")
    op.execute("GRANT USAGE ON SEQUENCE logins_id_seq TO trading_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON passkey_credentials, enrollment_tokens TO trading_app"
    )
    op.drop_index("ix_audit_log_user_at", table_name="audit_log")
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("users")
