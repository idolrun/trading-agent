"""laya interpreter

Laya (a local typed-decision model) answers when Jev gave no answer: rows written by it carry
provider `laya`, signals interpreter `laya` and link method `laya`.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDERS = ["typesafe", "anthropic", "openai", "deepseek", "openai_compat", "bedrock"]
INTERPRETERS = ["jev", "llm", "none"]
METHODS = ["reply", "jev", "llm", "user_correction", "code"]


def _set(table: str, column: str, values: list[str]) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{column}"), table, type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(column, table, f"{column} IN ({listed})")


def upgrade() -> None:
    _set("model_calls", "provider", [*PROVIDERS, "laya"])
    _set("signals", "interpreter", [*INTERPRETERS, "laya"])
    _set("message_links", "method", [*METHODS, "laya"])


def downgrade() -> None:
    _set("message_links", "method", METHODS)
    _set("signals", "interpreter", INTERPRETERS)
    _set("model_calls", "provider", PROVIDERS)
