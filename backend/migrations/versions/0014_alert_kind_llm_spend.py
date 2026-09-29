"""alert kind llm_spend

F049: month-to-date LLM spend reached 80 % / 100 % of `llm_monthly_budget_usd` (C44) ->
alert of kind `llm_spend` (no existing kind fit).

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
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
    "eod_forced_close",
]


def _set_kinds(kinds: list[str]) -> None:
    op.drop_constraint(op.f("ck_alerts_kind"), "alerts", type_="check")
    listed = ", ".join(f"'{k}'" for k in kinds)
    op.create_check_constraint("kind", "alerts", f"kind IN ({listed})")


def upgrade() -> None:
    _set_kinds([*KINDS, "llm_spend"])


def downgrade() -> None:
    _set_kinds(KINDS)
