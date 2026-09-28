"""F008: alerts and system flags."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg import errors
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.models import Alert, AlertKind, Severity
from app.alerts.service import AlertAlreadyAcknowledged, AlertNotFound, AlertService
from app.db import create_session_factory, transaction
from app.main import libpq_dsn
from app.runtime_config.models import Actor
from app.system.flags import FlagService
from app.system.models import FlagName, SystemFlagEvent
from tests.integration.conftest import _url
from tests.integration.schema.helpers import add_session, expect_error


@pytest.fixture
async def services(owner_engine: object) -> AsyncIterator[tuple[AlertService, FlagService]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    factory = create_session_factory(engine)
    alerts = AlertService(factory)
    yield alerts, FlagService(factory, alerts)
    await engine.dispose()


async def count(db: AsyncSession, model: type) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0


async def test_dedupe_key_gives_one_row(
    services: tuple[AlertService, FlagService], db_session: AsyncSession
) -> None:
    alerts, _ = services
    first = await alerts.raise_(
        AlertKind.FEED_SILENCE, Severity.URGENT, "silent", dedupe_key="feed_silence:1"
    )
    second = await alerts.raise_(
        AlertKind.FEED_SILENCE, Severity.URGENT, "silent", dedupe_key="feed_silence:1"
    )
    assert first is not None and second is None
    assert await count(db_session, Alert) == 1


async def test_raise_joins_caller_transaction(
    services: tuple[AlertService, FlagService], db_session: AsyncSession
) -> None:
    alerts, _ = services
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    with pytest.raises(RuntimeError):
        async with AsyncSession(engine) as caller, transaction(caller):
            await alerts.raise_(AlertKind.POISON, Severity.WARNING, "poison", db=caller)
            raise RuntimeError("caller rolls back")
    await engine.dispose()
    assert await count(db_session, Alert) == 0


async def test_kill_switch_blocks_entries_and_is_audited(
    services: tuple[AlertService, FlagService], db_session: AsyncSession
) -> None:
    _, flags = services
    assert await flags.entries_allowed() == (True, None)

    assert await flags.set(FlagName.KILL_SWITCH, True, "manual stop", Actor.CLI)
    assert not await flags.set(FlagName.KILL_SWITCH, True, "again", Actor.CLI)  # idempotent
    assert await flags.is_active(FlagName.KILL_SWITCH)
    assert await flags.entries_allowed() == (False, "kill_switch: manual stop")

    await flags.set(FlagName.ENTRIES_BLOCKED, True, "reconcile mismatch", Actor.SYSTEM)
    allowed, reason = await flags.entries_allowed()
    assert not allowed
    assert reason == "kill_switch: manual stop; entries_blocked: reconcile mismatch"

    assert await flags.set(FlagName.KILL_SWITCH, False, "resume", Actor.CLI)
    assert await flags.set(FlagName.ENTRIES_BLOCKED, False, "reconciled", Actor.SYSTEM)
    assert await flags.entries_allowed() == (True, None)

    assert await count(db_session, SystemFlagEvent) == 4
    rows = (await db_session.scalars(select(Alert).order_by(Alert.created_at))).all()
    assert [(a.kind, a.severity) for a in rows] == [
        ("system_flag", "urgent"),
        ("system_flag", "urgent"),
        ("system_flag", "info"),
        ("system_flag", "info"),
    ]


async def test_non_entry_flags_do_not_block_entries(
    services: tuple[AlertService, FlagService],
) -> None:
    _, flags = services
    await flags.set(FlagName.CATCH_UP, True, "backlog 40", Actor.SYSTEM)
    assert await flags.entries_allowed() == (True, None)


async def test_acknowledge_once(
    services: tuple[AlertService, FlagService], db_session: AsyncSession
) -> None:
    alerts, _ = services
    session_id = await add_session(db_session)
    alert_id = await alerts.raise_(AlertKind.POISON, Severity.WARNING, "poison")
    assert alert_id is not None
    await alerts.acknowledge(alert_id, session_id)
    with pytest.raises(AlertAlreadyAcknowledged):
        await alerts.acknowledge(alert_id, session_id)
    with pytest.raises(AlertNotFound):
        await alerts.acknowledge(uuid.uuid4(), session_id)


async def test_notify_on_commit(services: tuple[AlertService, FlagService]) -> None:
    alerts, flags = services
    async with await psycopg.AsyncConnection.connect(
        libpq_dsn(_url("test_database_url")), autocommit=True
    ) as conn:
        await conn.execute("LISTEN alerts")
        await conn.execute("LISTEN flags")
        alert_id = await alerts.raise_(AlertKind.POISON, Severity.WARNING, "poison")
        await flags.set(FlagName.PARSER_DEGRADED, True, "LLM chain down", Actor.SYSTEM)
        received: list[tuple[str, str]] = []
        async with asyncio.timeout(5):
            async for n in conn.notifies():
                received.append((n.channel, n.payload))
                if len(received) == 3:
                    break
    assert ("alerts", str(alert_id)) in received
    assert ("flags", "parser_degraded") in received


async def test_app_role_cannot_update_alerts(db_session: AsyncSession) -> None:
    await expect_error(db_session, errors.InsufficientPrivilege, "UPDATE alerts SET message = 'x'")
