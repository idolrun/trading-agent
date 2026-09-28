"""LangGraph persistence in schema `langgraph` (TRD §5.1, §5.3): checkpointer (where a run is)
and store (provider profiles). Domain tables stay the source of truth.

Tables are created once by the owner role: `uv run python -m app.graph.checkpointer setup`
(the runtime role gets DML through migration 0001's default privileges).
"""

import asyncio
import sys
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

SEARCH_PATH = "-c search_path=langgraph"
CONNECTION: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
    "options": SEARCH_PATH,
}


def open_pool(dsn: str, max_size: int = 5) -> AsyncConnectionPool[Any]:
    """Caller opens (`await pool.open()`) and closes it."""
    return AsyncConnectionPool(dsn, kwargs=CONNECTION, max_size=max_size, open=False)


def saver_and_store(
    pool: AsyncConnectionPool[Any],
) -> tuple[AsyncPostgresSaver, AsyncPostgresStore]:
    return AsyncPostgresSaver(pool), AsyncPostgresStore(pool)


async def setup(owner_dsn: str) -> None:
    """Idempotent: creates / migrates the checkpoint and store tables."""
    async with await AsyncConnection.connect(
        owner_dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row, options=SEARCH_PATH
    ) as conn:
        await AsyncPostgresSaver(conn).setup()
        await AsyncPostgresStore(conn).setup()


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


if __name__ == "__main__":
    if sys.argv[1:] != ["setup"]:
        sys.exit("usage: python -m app.graph.checkpointer setup")
    asyncio.run(setup(_libpq(str(get_settings().database_owner_url))))
    print("langgraph checkpointer and store are set up")
