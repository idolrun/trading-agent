"""User, session and audit-log queries. No policy here."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.pagination import cut, keyset
from app.auth.models import AuditAction, AuditLog, AuthSession, User


async def user_by_email(db: AsyncSession, email: str) -> User | None:
    row: User | None = await db.scalar(select(User).where(col(User.email) == email))
    return row


async def user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    row: User | None = await db.get(User, user_id)
    return row


async def all_users(db: AsyncSession) -> list[User]:
    rows = await db.scalars(select(User).order_by(col(User.created_at)))
    return list(rows.all())


async def active_user_count(db: AsyncSession) -> int:
    count = await db.scalar(select(func.count()).select_from(User).where(col(User.is_active)))
    return count or 0


async def update_user(db: AsyncSession, user_id: uuid.UUID, now: datetime, **values: Any) -> None:
    await db.execute(
        update(User).where(col(User.user_id) == user_id).values(updated_at=now, **values)
    )


async def session_and_user(
    db: AsyncSession, token_hash: bytes
) -> tuple[AuthSession, User | None] | None:
    row = (
        await db.execute(
            select(AuthSession, User)
            .join(User, col(User.user_id) == col(AuthSession.user_id), isouter=True)
            .where(col(AuthSession.token_hash) == token_hash)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def touch_session(db: AsyncSession, session_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(AuthSession)
        .where(col(AuthSession.session_id) == session_id)
        .values(last_seen_at=now)
    )


async def revoke_session(db: AsyncSession, session_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(AuthSession)
        .where(col(AuthSession.session_id) == session_id, col(AuthSession.revoked_at).is_(None))
        .values(revoked_at=now)
    )


async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, now: datetime, *, keep: uuid.UUID | None = None
) -> None:
    """Revoke every live session of a user, except `keep`."""
    stmt = update(AuthSession).where(
        col(AuthSession.user_id) == user_id, col(AuthSession.revoked_at).is_(None)
    )
    if keep is not None:
        stmt = stmt.where(col(AuthSession.session_id) != keep)
    await db.execute(stmt.values(revoked_at=now))


async def set_step_up(db: AsyncSession, session_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(AuthSession).where(col(AuthSession.session_id) == session_id).values(step_up_at=now)
    )


async def consume_step_up(db: AsyncSession, session_id: uuid.UUID, since: datetime) -> bool:
    """Atomically use a step-up made at/after `since`; each step-up authorizes one action."""
    used = await db.scalar(
        update(AuthSession)
        .where(col(AuthSession.session_id) == session_id, col(AuthSession.step_up_at) >= since)
        .values(step_up_at=None)
        .returning(col(AuthSession.session_id))
    )
    return used is not None


async def list_audit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: AuditAction | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AuditLog], str | None]:
    query = select(AuditLog)
    if user_id is not None:
        query = query.where(col(AuditLog.user_id) == user_id)
    if action is not None:
        query = query.where(col(AuditLog.action) == action)
    query = keyset(query, AuditLog.at, AuditLog.id, cursor, limit, int)
    rows = list((await db.scalars(query)).all())
    return cut(rows, limit, lambda r: (r.at, r.id))
