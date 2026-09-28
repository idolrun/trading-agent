import pytest
from alembic import command
from psycopg import errors
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import alembic_config


async def test_app_role_connects(db_session: AsyncSession) -> None:
    assert await db_session.scalar(text("SELECT current_user")) == "trading_app"
    assert await db_session.scalar(text("SELECT 1")) == 1


async def test_langgraph_schema_exists(db_session: AsyncSession) -> None:
    found = await db_session.scalar(
        text("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'langgraph'")
    )
    assert found == 1


async def test_app_role_cannot_create_table(db_session: AsyncSession) -> None:
    with pytest.raises(ProgrammingError) as exc:
        await db_session.execute(text("CREATE TABLE public.nope (id int)"))
    assert isinstance(exc.value.orig, errors.InsufficientPrivilege)


def test_migrations_down_and_up() -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
