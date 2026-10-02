"""link evidence + deterministic resolution methods (F069)

`message_links.evidence`: why the link was made or refused (resolver rule, candidates, a
rejected model hint and the reason). Methods `thread` and `contract` join `reply`.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METHODS = ["reply", "jev", "llm", "laya", "user_correction", "code"]


def _set(values: list[str]) -> None:
    op.drop_constraint(op.f("ck_message_links_method"), "message_links", type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint("method", "message_links", f"method IN ({listed})")


def upgrade() -> None:
    op.add_column("message_links", sa.Column("evidence", JSONB(none_as_null=True), nullable=True))
    _set([*METHODS, "thread", "contract"])


def downgrade() -> None:
    op.execute("UPDATE message_links SET method = 'code' WHERE method IN ('thread', 'contract')")
    _set(METHODS)
    op.drop_column("message_links", "evidence")
