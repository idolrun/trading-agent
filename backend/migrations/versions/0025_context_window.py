"""explicit context window in the stored runtime config (F073)

`context_recent_messages` had no default: live interpretation then read 0 earlier messages
while evalkit and labeling read 5. The schema default is now 5; a stored null becomes 5, once,
with an audit row. A value already set is untouched.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-02
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WINDOW = 5
REASON = "migration 0025: one context window for live, replay and labeling"


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT version, document FROM config WHERE id = 1")).first()
    if row is None:
        return  # no config yet: the schema default applies
    version, before = row
    if before.get("context_recent_messages") is not None:
        return
    after = {**before, "context_recent_messages": WINDOW}
    diff = {"context_recent_messages": {"before": None, "after": WINDOW}}
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


def downgrade() -> None:
    pass  # a window of 5 stays valid under the old schema
