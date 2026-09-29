"""Append-only audit log of security-sensitive and user-initiated trading actions."""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import repo
from app.auth.models import AuditAction, AuditLog
from app.db import in_transaction

if TYPE_CHECKING:
    from app.auth.service import AuthContext


@dataclass(frozen=True)
class Client:
    ip: str | None
    user_agent: str | None
    request_id: str | None = None


class AuditService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record(
        self,
        action: AuditAction,
        client: Client | None = None,
        *,
        user_id: uuid.UUID | None = None,
        email: str | None = None,
        resource_type: str | None = None,
        resource_id: object = None,
        details: dict[str, Any] | None = None,
        db: AsyncSession | None = None,
    ) -> None:
        """Own transaction unless `db` is given (then it commits with the caller's)."""
        async with in_transaction(self._sessions, db) as session:
            session.add(
                AuditLog(
                    action=action,
                    user_id=user_id,
                    user_email=email,
                    resource_type=resource_type,
                    resource_id=None if resource_id is None else str(resource_id),
                    ip=client.ip if client else None,
                    request_id=client.request_id if client else None,
                    details=details or {},
                )
            )

    async def by(
        self,
        ctx: "AuthContext",
        client: Client,
        action: AuditAction,
        resource_type: str,
        resource_id: object = None,
        **details: Any,
    ) -> None:
        """A signed-in user's action (trade decisions, manual ops)."""
        await self.record(
            action,
            client,
            user_id=ctx.user_id,
            email=ctx.email,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )

    async def entries(
        self,
        *,
        user_id: uuid.UUID | None = None,
        action: AuditAction | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[AuditLog], str | None]:
        async with in_transaction(self._sessions, None) as db:
            return await repo.list_audit(
                db, user_id=user_id, action=action, cursor=cursor, limit=limit
            )
