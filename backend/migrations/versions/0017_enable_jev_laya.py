"""enable Jev and Laya in the stored runtime config

Jev is the primary interpreter and Laya its fallback, so both are on by default (schema
defaults). A config row written before that stores them as off: switch them on, once, with an
audit row. Everything else in the document (timeouts, thresholds, the LLM chain) is untouched.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-02
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASON = "migration 0017: Jev primary + Laya fallback on by default"


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT version, document FROM config WHERE id = 1")).first()
    if row is None:
        return  # no config yet: the schema defaults apply
    version, before = row
    after = json.loads(json.dumps(before))
    jev, laya = after.setdefault("jev", {}), after.setdefault("laya", {})
    jev["enabled"] = True
    if jev.get("model") is None:
        jev["model"] = "jev-latest"
    laya["enabled"] = True
    if after == before:
        return  # already on: running it twice changes nothing
    diff = {
        k: {"before": before.get(k), "after": after[k]}
        for k in ("jev", "laya")
        if before.get(k) != after[k]
    }
    conn.execute(
        sa.text(
            "UPDATE config SET version = :v, document = CAST(:d AS jsonb), updated_at = now() WHERE id = 1"
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
    """Data migration: the previous values are in `config_changes`."""
