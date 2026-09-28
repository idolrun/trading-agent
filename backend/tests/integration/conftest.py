"""Real PostgreSQL 17 (`trading_test`). Needs `docker compose up -d postgres`.

Migrated once per session as trading_owner; tests run as trading_app. Tables are truncated
after each test (not rolled back): features rely on commits (NOTIFY, advisory locks, separate
connections).
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

# Structural rows seeded by migrations survive truncation (flags are reset instead).
KEEP_TABLES = {"alembic_version", "system_flags"}


def _url(name: str) -> str:
    url = getattr(get_settings(), name)
    if url is None:
        pytest.fail(f"{name.upper()} is not set (see .env.example)")
    return str(url)


def alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _url("test_database_owner_url").replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    command.upgrade(alembic_config(), "head")
    engine = create_engine(_url("test_database_owner_url"), poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(owner_engine: Engine) -> Iterator[None]:
    yield
    with owner_engine.begin() as conn:
        tables = conn.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).all()
        truncate = [t for t in tables if t not in KEEP_TABLES]
        if truncate:
            conn.execute(text(f"TRUNCATE {', '.join(truncate)} RESTART IDENTITY CASCADE"))
        if "system_flags" in tables:
            conn.execute(text("UPDATE system_flags SET active = false, reason = NULL"))


@pytest.fixture
async def db_session(owner_engine: Engine) -> AsyncIterator[AsyncSession]:
    """Session as the runtime role trading_app (DML only)."""
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
