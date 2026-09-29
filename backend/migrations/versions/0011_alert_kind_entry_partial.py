"""alert kind entry_partial

F041: an entry whose remainder was cancelled after M seconds with some contracts filled
(C22) raises an info alert of kind `entry_partial` (no existing kind fit).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
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
]


def _set_kinds(kinds: list[str]) -> None:
    op.drop_constraint(op.f("ck_alerts_kind"), "alerts", type_="check")
    listed = ", ".join(f"'{k}'" for k in kinds)
    op.create_check_constraint("kind", "alerts", f"kind IN ({listed})")


def upgrade() -> None:
    _set_kinds([*KINDS, "entry_partial"])


def downgrade() -> None:
    _set_kinds(KINDS)
