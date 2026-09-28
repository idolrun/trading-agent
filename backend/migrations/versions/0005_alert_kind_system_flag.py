"""alert kind system_flag

F008: every system flag transition raises an alert of kind `system_flag`
(kill switch and entries-blocked have no other kind to use).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
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
]


def _set_kinds(kinds: list[str]) -> None:
    op.drop_constraint(op.f("ck_alerts_kind"), "alerts", type_="check")
    listed = ", ".join(f"'{k}'" for k in kinds)
    op.create_check_constraint("kind", "alerts", f"kind IN ({listed})")


def upgrade() -> None:
    _set_kinds([*KINDS, "system_flag"])


def downgrade() -> None:
    _set_kinds(KINDS)
