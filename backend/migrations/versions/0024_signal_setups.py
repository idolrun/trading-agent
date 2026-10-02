"""watch / conditional setups and re-entries on provider positions (F072)

A provider position row is the provider's ongoing signal: `watch` / `conditional` (a setup that
may name only part of a contract, never a card) -> `open` -> `trimmed` -> `closed`. A re-entry
opens a new row pointing at the one it re-enters.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

T = "provider_positions"
STATUSES = ["open", "trimmed", "closed"]
CHANGES = [
    "open",
    "add",
    "trim",
    "exit",
    "stop_set",
    "stop_move",
    "stop_cancel",
    "tp_set",
    "correct",
    "close",
]
COMPLETE = (
    "(instrument = 'option' AND expiry IS NOT NULL AND strike IS NOT NULL"
    " AND option_right IS NOT NULL)"
    " OR (instrument = 'stock' AND expiry IS NULL AND strike IS NULL"
    " AND option_right IS NULL)"
)


def _in(table: str, column: str, values: list[str]) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{column}"), table, type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(column, table, f"{column} IN ({listed})")


def upgrade() -> None:
    _in(T, "status", ["watch", "conditional", *STATUSES])
    _in("provider_position_events", "change", [*CHANGES, "watch", "condition_set"])
    op.drop_constraint(op.f(f"ck_{T}_contract_shape"), T, type_="check")
    op.create_check_constraint(
        "contract_shape", T, f"status IN ('watch', 'conditional') OR {COMPLETE}"
    )
    op.alter_column(T, "instrument", nullable=True)
    op.create_check_constraint(
        "instrument_unless_setup", T, "instrument IS NOT NULL OR status IN ('watch', 'conditional')"
    )
    op.add_column(T, sa.Column("condition_text", sa.Text(), nullable=True))
    op.add_column(T, sa.Column("reentry_of_position_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_{T}_reentry_of_position_id"),
        T,
        T,
        ["reentry_of_position_id"],
        ["provider_position_id"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM provider_position_events WHERE change IN ('watch', 'condition_set')")
    op.execute(
        "DELETE FROM message_links WHERE provider_position_id IN (SELECT provider_position_id"
        " FROM provider_positions WHERE status IN ('watch', 'conditional'))"
    )
    op.execute("DELETE FROM provider_positions WHERE status IN ('watch', 'conditional')")
    op.drop_constraint(op.f(f"fk_{T}_reentry_of_position_id"), T, type_="foreignkey")
    op.drop_column(T, "reentry_of_position_id")
    op.drop_column(T, "condition_text")
    op.drop_constraint(op.f(f"ck_{T}_instrument_unless_setup"), T, type_="check")
    op.alter_column(T, "instrument", nullable=False)
    op.drop_constraint(op.f(f"ck_{T}_contract_shape"), T, type_="check")
    op.create_check_constraint("contract_shape", T, COMPLETE)
    _in("provider_position_events", "change", CHANGES)
    _in(T, "status", STATUSES)
