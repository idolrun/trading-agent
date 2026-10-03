"""One running instance per database (F076, TRD §1 "one process, one database").

Per-lane ordering (dispatcher), the account entry gate (approvals) and the bracket locks are
in-process `asyncio.Lock`s. A second process on the same database would bypass all of them, so
the background services refuse to start unless they hold a session-level Postgres advisory
lock. The lock lives on its own connection and is released automatically if that connection
drops (crash, failover), so a restarted instance can take it again.
"""

import logging

import psycopg

logger = logging.getLogger(__name__)

LOCK_KEY = 7_726_164_420_001  # arbitrary, fixed: "the trading-agent background services"


class SecondInstance(RuntimeError):
    """Another process already runs the background services on this database."""


async def hold_instance_lock(dsn: str) -> psycopg.AsyncConnection[tuple[object, ...]]:
    """Take the lock or raise `SecondInstance`. Keep the returned connection open for the
    process's lifetime and close it at shutdown (that releases the lock)."""
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        row = await (await conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))).fetchone()
    except Exception:
        await conn.close()
        raise
    if not row or not row[0]:
        await conn.close()
        raise SecondInstance(
            "another instance already runs the background services on this database; "
            "run exactly one (uvicorn --workers 1, one replica)"
        )
    logger.info("instance lock held")
    return conn
