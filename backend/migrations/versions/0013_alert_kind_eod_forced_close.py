"""alert kind eod_forced_close

F045: an expiring lot left unanswered at cutoff 2 is closed automatically (PRD 7.6, §9) ->
alert of kind `eod_forced_close` (no existing kind fit).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = [
    "feed_silence",
    "parser_degraded",
    "poison",
    "reconcile_mismatch",
    "catch_up",
    "late_correction",
    "message_deleted",
    "risk_limit",
    "exit_floor",
    "provider_disabled",
    "market_data",
    "invariant_block",
    "note_hidden",
    "system_flag",
    "auth_anomaly",
    "message_api",
    "timer_failed",
    "order_failed",
    "entry_partial",
    "underlying_stop",
]


def _set_kinds(kinds: list[str]) -> None:
    op.drop_constraint(op.f("ck_alerts_kind"), "alerts", type_="check")
    listed = ", ".join(f"'{k}'" for k in kinds)
    op.create_check_constraint("kind", "alerts", f"kind IN ({listed})")


def upgrade() -> None:
    _set_kinds([*KINDS, "eod_forced_close"])


def downgrade() -> None:
    _set_kinds(KINDS)
