"""Config row + audit writes (transaction T8). No validation rules here."""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningInsert, ReturningUpdate
from sqlmodel import col

from app.runtime_config.models import Actor, ConfigChange, ConfigRow


class VersionConflict(Exception):
    """`expected_version` no longer matches the stored version (someone else won)."""


async def load(session: AsyncSession) -> ConfigRow | None:
    return await session.get(ConfigRow, 1)


async def save(
    session: AsyncSession,
    *,
    document: dict[str, Any],
    diff: dict[str, Any],
    expected_version: int,
    actor: Actor,
    reason: str,
    session_id: uuid.UUID | None,
) -> int:
    """Write the new version, its audit row and `NOTIFY config`. Caller owns the transaction."""
    stmt: ReturningInsert[tuple[int]] | ReturningUpdate[tuple[int]]
    if expected_version == 0:
        stmt = (
            insert(ConfigRow)
            .values(id=1, version=1, document=document)
            .on_conflict_do_nothing()
            .returning(col(ConfigRow.version))
        )
    else:
        stmt = (
            update(ConfigRow)
            .where(col(ConfigRow.id) == 1, col(ConfigRow.version) == expected_version)
            .values(version=expected_version + 1, document=document, updated_at=func.now())
            .returning(col(ConfigRow.version))
        )
    version: int | None = await session.scalar(stmt)
    if version is None:
        raise VersionConflict(expected_version)
    session.add(
        ConfigChange(
            version=version,
            document=document,
            diff=diff,
            actor=actor,
            reason=reason,
            session_id=session_id,
        )
    )
    await session.execute(select(func.pg_notify("config", str(version))))
    return version
