"""card reason: order_rejected

An approved card whose order the broker refuses is invalidated with this reason instead of
being shown as submitted.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = [
    "created",
    "approved",
    "rejected",
    "timeout",
    "price_band",
    "superseded",
    "correction",
    "deleted",
    "restart",
    "revalidate_failed",
    "edited",
    "review_completed",
    "submitted",
]


def _set(values: list[str]) -> None:
    op.drop_constraint(op.f("ck_card_events_reason_code"), "card_events", type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint("reason_code", "card_events", f"reason_code IN ({listed})")


def upgrade() -> None:
    _set([*REASONS, "order_rejected"])


def downgrade() -> None:
    op.execute(
        "UPDATE card_events SET reason_code = 'revalidate_failed' WHERE reason_code = 'order_rejected'"
    )
    _set(REASONS)
