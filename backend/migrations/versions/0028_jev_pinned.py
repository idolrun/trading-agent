"""pin Jev to a concrete model and record the model that answered (F080)

- `model_calls.model_returned`: the concrete model the API reports (`SystemOneResponse.model`).
- Stored runtime config: `jev.model` `jev-latest` (or unset) -> `jev-1.13.0`, the version the
  aliases pointed to on 2026-10-03 and the thresholds were measured on; once, with an audit row.
  Another explicit model is untouched.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-03
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PINNED = "jev-1.13.0"
REASON = "migration 0028: Jev pinned to jev-1.13.0 (aliases move; thresholds were tuned on it)"


def upgrade() -> None:
    op.add_column("model_calls", sa.Column("model_returned", sa.String(), nullable=True))
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT version, document FROM config WHERE id = 1")).first()
    if row is None:
        return  # no config yet: the schema default (pinned) applies
    version, before = row
    jev = dict(before.get("jev") or {})
    if jev.get("model") not in (None, "jev-latest"):
        return
    after_jev = {**jev, "model": PINNED}
    after = {**before, "jev": after_jev}
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
        {
            "v": version + 1,
            "d": json.dumps(after),
            "diff": json.dumps({"jev": {"before": jev, "after": after_jev}}),
            "r": REASON,
        },
    )
    conn.execute(sa.text("SELECT pg_notify('config', :v)"), {"v": str(version + 1)})


def downgrade() -> None:
    op.drop_column("model_calls", "model_returned")  # the pinned model stays valid config
