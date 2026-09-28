"""roles and schemas

Schema `langgraph` for LangGraph checkpointer/store tables (created later by `setup()` as
trading_owner, F035) and schema usage for the runtime role. Tables in `public` get explicit
per-table grants in their own migrations (no default privileges there, so audit tables stay
insert-only). Roles themselves are created by infra/postgres/init/01-roles.sh.

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA langgraph AUTHORIZATION trading_owner")
    op.execute("GRANT USAGE ON SCHEMA public, langgraph TO trading_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA langgraph "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO trading_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA langgraph "
        "GRANT USAGE, SELECT ON SEQUENCES TO trading_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA langgraph "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM trading_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA langgraph "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM trading_app"
    )
    op.execute("REVOKE USAGE ON SCHEMA public, langgraph FROM trading_app")
    # CASCADE: LangGraph's own tables live here (created by `setup()`, not Alembic, F035).
    op.execute("DROP SCHEMA langgraph CASCADE")
