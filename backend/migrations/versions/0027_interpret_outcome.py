"""interpretation outcome on the inbox row, Jev queue timeout, Jev time budget (F078)

- `inbox.interpret_outcome` + `interpret_reasons`: how interpretation of an event ended and why
  each interpreter did not settle it (fast / jev / fallback success, review, validation
  failure, scope violation).
- `model_calls.outcome` + `queue_timeout`: no Jev limiter slot within `queue_timeout_ms`.
- Stored runtime config: Jev request timeout 700 ms, limiter wait 250 ms, step budget 1200 ms
  (user decision 2026-10-03, from 510 real calls: p50 353 ms, p95 450 ms; 0 finished under the
  old 300 ms), once, with an audit row.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-03
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOMES = [
    "ok",
    "timeout",
    "rate_limited",
    "server_error",
    "connection_error",
    "schema_invalid",
    "empty",
    "refusal",
    "scope_blocked",
    "span_rejected",
    "circuit_open",
    "busy",
]
INTERPRET = [
    "fast_success",
    "jev_success",
    "fallback_success",
    "review",
    "validation_failure",
    "scope_violation",
    "fixture",
]
BUDGET = {"timeout_ms": 700, "queue_timeout_ms": 250, "budget_ms": 1200}
REASON = "migration 0027: Jev time budget (700 ms request, 250 ms queue, 1200 ms step)"


def _set(table: str, column: str, values: list[str]) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{column}"), table, type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(column, table, f"{column} IN ({listed})")


def _write_config(after: dict[str, object], version: int, diff: dict[str, object]) -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE config SET version = :v, document = CAST(:d AS jsonb), updated_at = now()"
            " WHERE id = 1"
        ),
        {"v": version + 1, "d": json.dumps(after)},
    )
    conn.execute(
        sa.text(
            "INSERT INTO config_changes (version, document, diff, actor, reason) "
            "VALUES (:v, CAST(:d AS jsonb), CAST(:diff AS jsonb), 'system', :r)"
        ),
        {"v": version + 1, "d": json.dumps(after), "diff": json.dumps(diff), "r": REASON},
    )
    conn.execute(sa.text("SELECT pg_notify('config', :v)"), {"v": str(version + 1)})


def upgrade() -> None:
    op.add_column("inbox", sa.Column("interpret_outcome", sa.String(), nullable=True))
    op.add_column("inbox", sa.Column("interpret_reasons", JSONB(), nullable=True))
    listed = ", ".join(f"'{v}'" for v in INTERPRET)
    op.create_check_constraint("interpret_outcome", "inbox", f"interpret_outcome IN ({listed})")
    _set("model_calls", "outcome", [*OUTCOMES, "queue_timeout"])

    row = (
        op.get_bind().execute(sa.text("SELECT version, document FROM config WHERE id = 1")).first()
    )
    if row is None:
        return  # no config yet: the schema defaults apply
    version, before = row
    jev = dict(before.get("jev") or {})
    after_jev = {**jev, **BUDGET}
    if after_jev == jev:
        return
    _write_config(
        {**before, "jev": after_jev}, version, {"jev": {"before": jev, "after": after_jev}}
    )


def downgrade() -> None:
    row = (
        op.get_bind().execute(sa.text("SELECT version, document FROM config WHERE id = 1")).first()
    )
    if row is not None:  # the old schema forbids the new keys
        version, before = row
        jev = dict(before.get("jev") or {})
        after_jev = {k: v for k, v in jev.items() if k not in ("queue_timeout_ms", "budget_ms")}
        if after_jev != jev:
            _write_config(
                {**before, "jev": after_jev}, version, {"jev": {"before": jev, "after": after_jev}}
            )
    op.execute("UPDATE model_calls SET outcome = 'timeout' WHERE outcome = 'queue_timeout'")
    _set("model_calls", "outcome", OUTCOMES)
    op.drop_constraint(op.f("ck_inbox_interpret_outcome"), "inbox", type_="check")
    op.drop_column("inbox", "interpret_reasons")
    op.drop_column("inbox", "interpret_outcome")
