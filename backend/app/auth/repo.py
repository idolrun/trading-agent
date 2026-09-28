"""Credential, enrolment-token, session and login-audit queries. No policy here."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.auth.models import AuthSession, EnrollmentToken, Login, LoginOutcome, PasskeyCredential


async def add_enrollment_token(db: AsyncSession, token_hash: bytes, expires_at: datetime) -> None:
    db.add(EnrollmentToken(token_hash=token_hash, expires_at=expires_at))


async def enrollment_token_usable(db: AsyncSession, token_hash: bytes, now: datetime) -> bool:
    found = await db.scalar(
        select(col(EnrollmentToken.token_hash)).where(
            col(EnrollmentToken.token_hash) == token_hash,
            col(EnrollmentToken.used_at).is_(None),
            col(EnrollmentToken.expires_at) > now,
        )
    )
    return found is not None


async def consume_enrollment_token(db: AsyncSession, token_hash: bytes, now: datetime) -> bool:
    """Atomically mark the token used; False if it was already used or has expired."""
    used = await db.scalar(
        update(EnrollmentToken)
        .where(
            col(EnrollmentToken.token_hash) == token_hash,
            col(EnrollmentToken.used_at).is_(None),
            col(EnrollmentToken.expires_at) > now,
        )
        .values(used_at=now)
        .returning(col(EnrollmentToken.token_hash))
    )
    return used is not None


async def active_credentials(db: AsyncSession) -> list[PasskeyCredential]:
    rows = await db.scalars(
        select(PasskeyCredential).where(col(PasskeyCredential.revoked_at).is_(None))
    )
    return list(rows.all())


async def all_credentials(db: AsyncSession) -> list[PasskeyCredential]:
    rows = await db.scalars(select(PasskeyCredential).order_by(col(PasskeyCredential.created_at)))
    return list(rows.all())


async def active_credential(db: AsyncSession, credential_id: bytes) -> PasskeyCredential | None:
    row: PasskeyCredential | None = await db.scalar(
        select(PasskeyCredential).where(
            col(PasskeyCredential.credential_id) == credential_id,
            col(PasskeyCredential.revoked_at).is_(None),
        )
    )
    return row


async def record_credential_use(
    db: AsyncSession, credential_id: bytes, sign_count: int, now: datetime
) -> None:
    await db.execute(
        update(PasskeyCredential)
        .where(col(PasskeyCredential.credential_id) == credential_id)
        .values(sign_count=sign_count, last_used_at=now)
    )


async def revoke_credential(db: AsyncSession, credential_id: bytes, now: datetime) -> bool:
    """Revoke a credential and every live session created with it."""
    revoked = await db.scalar(
        update(PasskeyCredential)
        .where(
            col(PasskeyCredential.credential_id) == credential_id,
            col(PasskeyCredential.revoked_at).is_(None),
        )
        .values(revoked_at=now)
        .returning(col(PasskeyCredential.credential_id))
    )
    await db.execute(
        update(AuthSession)
        .where(
            col(AuthSession.credential_id) == credential_id, col(AuthSession.revoked_at).is_(None)
        )
        .values(revoked_at=now)
    )
    return revoked is not None


async def session_by_token_hash(db: AsyncSession, token_hash: bytes) -> AuthSession | None:
    row: AuthSession | None = await db.scalar(
        select(AuthSession).where(col(AuthSession.token_hash) == token_hash)
    )
    return row


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


async def add_login(
    db: AsyncSession,
    outcome: LoginOutcome,
    *,
    ip: str | None,
    user_agent: str | None,
    credential_id: bytes | None = None,
    session_id: uuid.UUID | None = None,
) -> None:
    db.add(
        Login(
            outcome=outcome,
            credential_id=credential_id,
            session_id=session_id,
            ip=ip,
            user_agent=user_agent,
        )
    )
