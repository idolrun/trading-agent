"""Engine, sessions, transaction helper and shared column helpers. No queries here."""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    SmallInteger,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlmodel import AutoString, Field, SQLModel

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.database_url), pool_pre_ping=True, pool_size=settings.db_pool_size
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # The ORM resolves every FK target at flush time, so all tables must be registered
    # in any process that writes through a session (app, CLIs, workers).
    import app.db_models  # noqa: F401 — deferred: models import this module

    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, never shared across tasks."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on error."""
    async with session.begin():
        yield session


# --- Table-definition helpers shared by every `models.py` (database-schema.md §1) ---

SQLModel.metadata.naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

TIMESTAMPTZ = DateTime(timezone=True)


@compiles(AutoString, "postgresql")
def _auto_string_as_text(_element: AutoString, _compiler: Any, **_kw: Any) -> str:
    """SQLModel maps `str` to VARCHAR; the schema convention is `text` (same semantics in PG)."""
    return "TEXT"


def utcnow() -> datetime:
    return datetime.now(UTC)


def check_in(column: str, values: Iterable[str], name: str | None = None) -> CheckConstraint:
    """`CHECK (column IN (...))` from a StrEnum, so DB and Python lists cannot drift."""
    listed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({listed})", name=name or column)


def positive_ids(*columns: str) -> list[CheckConstraint]:
    """Discord snowflakes are `bigint > 0`."""
    return [CheckConstraint(f"{c} > 0", name=f"{c}_positive") for c in columns]


def now_default() -> Any:
    """`timestamptz NOT NULL DEFAULT now()`; also set in Python so ORM inserts never send NULL."""
    return Field(
        default_factory=utcnow, sa_type=TIMESTAMPTZ, sa_column_kwargs={"server_default": func.now()}
    )


def timestamptz(*, nullable: bool = False) -> Any:
    return Field(default=None if nullable else ..., sa_type=TIMESTAMPTZ, nullable=nullable)


def flag(*, default: bool = False) -> Any:
    """`boolean NOT NULL` with a server default."""
    return Field(
        default=default, sa_column_kwargs={"server_default": true() if default else false()}
    )


def jsonb(*, nullable: bool = False) -> Any:
    return Field(default=None if nullable else ..., sa_type=JSONB, nullable=nullable)


def jsonb_object() -> Any:
    """`jsonb NOT NULL DEFAULT '{}'`."""
    return Field(
        default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": text("'{}'")}
    )


def bigint(*, nullable: bool = False, **kwargs: Any) -> Any:
    return Field(default=None if nullable else ..., sa_type=BigInteger, nullable=nullable, **kwargs)


def smallint(*, default: int | None = None, nullable: bool = False) -> Any:
    kwargs: dict[str, Any] = {}
    if default is not None:
        kwargs["sa_column_kwargs"] = {"server_default": str(default)}
    value = default if default is not None else (None if nullable else ...)
    return Field(default=value, sa_type=SmallInteger, nullable=nullable, **kwargs)


def array(item_type: Any) -> Any:
    """`<type>[] NOT NULL DEFAULT '{}'`."""
    return Field(
        default_factory=list,
        sa_type=ARRAY(item_type),
        sa_column_kwargs={"server_default": text("'{}'")},
    )


def price(*, nullable: bool = True) -> Any:
    """Per-share price or level: `numeric(12,4)`, Python `Decimal`."""
    return Field(
        default=None if nullable else ..., max_digits=12, decimal_places=4, nullable=nullable
    )


def inet(*, nullable: bool = True) -> Any:
    return Field(default=None, sa_type=INET, nullable=nullable)


def identity_pk() -> Any:
    """Audit-table surrogate key: `bigint GENERATED ALWAYS AS IDENTITY`."""
    return Field(
        default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )


def choice(**kwargs: Any) -> Any:
    """Enumerated text column (StrEnum in Python, `CHECK … IN` via `check_in`); never a PG ENUM."""
    return Field(sa_type=AutoString, **kwargs)
