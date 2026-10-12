"""Password auth use cases: login, sessions, step-up, password change, user management.
Every security-relevant attempt is audited."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import repo
from app.auth.audit import AuditService, Client
from app.auth.models import AuditAction, AuthSession, User
from app.auth.passwords import (
    MIN_LENGTH,
    hash_password_async,
    temporary_password,
    verify_password_async,
)
from app.auth.sessions import hash_token, new_token, session_problem
from app.config import Settings
from app.db import in_transaction
from app.markettime import Clock

logger = logging.getLogger(__name__)

__all__ = ["AuthContext", "AuthFailed", "AuthService", "Client"]


class AuthFailed(Exception):
    """Wrong email/password: deliberately says nothing about which."""


class InvalidPassword(Exception):
    pass


class UserExists(Exception):
    pass


class UserNotFound(Exception):
    pass


class CannotDeactivateSelf(Exception):
    pass


@dataclass(frozen=True)
class AuthContext:
    session_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    expires_at: datetime
    must_change_password: bool

    @classmethod
    def of(cls, row: AuthSession, user: User) -> "AuthContext":
        return cls(
            row.session_id,
            user.user_id,
            user.email,
            row.expires_at,
            user.must_change_password,
        )


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock,
        audit: AuditService,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self.clock = clock
        self.audit = audit

    # --- Login / sessions ---

    async def login(self, email: str, password: str, client: Client) -> tuple[str, AuthContext]:
        """Returns (cookie token, context). Raises AuthFailed for unknown, inactive or wrong."""
        email = normalize_email(email)
        async with in_transaction(self._sessions, None) as db:
            user = await repo.user_by_email(db, email)
        ok = await verify_password_async(password, user.password_hash if user else None)
        if user is None or not ok or not user.is_active:
            await self.audit.record(AuditAction.LOGIN_FAILED, client, details={"email": email})
            raise AuthFailed
        token, now = new_token(), self.clock.now()
        row = AuthSession(
            token_hash=hash_token(token),
            user_id=user.user_id,
            expires_at=now + timedelta(hours=self._settings.session_absolute_hours),
            last_seen_at=now,
            ip=client.ip,
            user_agent=client.user_agent,
        )
        async with in_transaction(self._sessions, None) as db:
            db.add(row)
            await db.flush()
            await repo.update_user(db, user.user_id, now, last_login_at=now)
            await self.audit.record(
                AuditAction.LOGIN_SUCCESS,
                client,
                user_id=user.user_id,
                email=user.email,
                resource_type="session",
                resource_id=row.session_id,
                db=db,
            )
        return token, AuthContext.of(row, user)

    async def authenticate(self, token: str | None, user_agent: str | None) -> AuthContext | None:
        """Validate a session cookie; revokes idle/expired/device-changed/deactivated sessions."""
        if not token:
            return None
        now = self.clock.now()
        async with in_transaction(self._sessions, None) as db:
            found = await repo.session_and_user(db, hash_token(token))
            if found is None:
                return None
            row, user = found
            problem = session_problem(row, now, user_agent, self._settings)
            if problem is None and (user is None or not user.is_active):
                problem = "user_inactive"
            if problem is not None or user is None:
                await repo.revoke_session(db, row.session_id, now)
                logger.info("session rejected", extra={"reason": problem})
                return None
            await repo.touch_session(db, row.session_id, now)
            return AuthContext.of(row, user)

    async def logout(self, ctx: AuthContext, client: Client) -> None:
        async with in_transaction(self._sessions, None) as db:
            await repo.revoke_session(db, ctx.session_id, self.clock.now())
            await self.audit.record(
                AuditAction.LOGOUT,
                client,
                user_id=ctx.user_id,
                email=ctx.email,
                resource_type="session",
                resource_id=ctx.session_id,
                db=db,
            )

    # --- Password change ---

    async def _check_password(self, ctx: AuthContext, password: str) -> bool:
        async with in_transaction(self._sessions, None) as db:
            user = await repo.user_by_id(db, ctx.user_id)
        return await verify_password_async(password, user.password_hash if user else None)

    async def change_password(
        self, ctx: AuthContext, current: str, new: str, client: Client
    ) -> None:
        """Raises AuthFailed (wrong current password) or InvalidPassword (new one unacceptable)."""
        if not await self._check_password(ctx, current):
            await self.audit.record(
                AuditAction.STEP_UP_FAILURE, client, user_id=ctx.user_id, email=ctx.email
            )
            raise AuthFailed
        if len(new) < MIN_LENGTH:
            raise InvalidPassword(f"password must be at least {MIN_LENGTH} characters")
        if new == current:
            raise InvalidPassword("choose a password different from the current one")
        hashed, now = await hash_password_async(new), self.clock.now()
        async with in_transaction(self._sessions, None) as db:
            await repo.update_user(
                db, ctx.user_id, now, password_hash=hashed, must_change_password=False
            )
            await repo.revoke_user_sessions(db, ctx.user_id, now, keep=ctx.session_id)
            await self.audit.record(
                AuditAction.PASSWORD_CHANGED, client, user_id=ctx.user_id, email=ctx.email, db=db
            )

    async def record_rate_limited(self, client: Client) -> None:
        await self.audit.record(AuditAction.RATE_LIMITED, client)

    # --- User management (any signed-in user; `actor` is None for the console CLI) ---

    async def users(self) -> list[User]:
        async with in_transaction(self._sessions, None) as db:
            return await repo.all_users(db)

    async def find_user(self, email: str) -> User | None:
        async with in_transaction(self._sessions, None) as db:
            return await repo.user_by_email(db, normalize_email(email))

    async def create_user(
        self, email: str, actor: AuthContext | None, client: Client | None
    ) -> tuple[User, str]:
        """Returns the user and its temporary password (shown once; only the hash is stored)."""
        email, temp = normalize_email(email), temporary_password()
        user = User(email=email, password_hash=await hash_password_async(temp))
        async with in_transaction(self._sessions, None) as db:
            if await repo.user_by_email(db, email) is not None:
                raise UserExists
            db.add(user)
            await db.flush()
            await self._audit_user(AuditAction.USER_CREATED, user, actor, client, db)
        return user, temp

    async def deactivate(self, user_id: uuid.UUID, actor: AuthContext, client: Client) -> User:
        if user_id == actor.user_id:
            raise CannotDeactivateSelf
        return await self._change_user(
            user_id, AuditAction.USER_DEACTIVATED, actor, client, is_active=False, revoke=True
        )

    async def reactivate(self, user_id: uuid.UUID, actor: AuthContext, client: Client) -> User:
        return await self._change_user(
            user_id, AuditAction.USER_REACTIVATED, actor, client, is_active=True
        )

    async def reset_password(
        self, user_id: uuid.UUID, actor: AuthContext | None, client: Client | None
    ) -> tuple[User, str]:
        temp = temporary_password()
        user = await self._change_user(
            user_id,
            AuditAction.PASSWORD_RESET,
            actor,
            client,
            password_hash=await hash_password_async(temp),
            must_change_password=True,
            revoke=True,
        )
        return user, temp

    async def set_password(self, user_id: uuid.UUID, password: str) -> User:
        """Console-only (development seed): a known password, no forced change."""
        return await self._change_user(
            user_id,
            AuditAction.PASSWORD_CHANGED,
            None,
            None,
            password_hash=await hash_password_async(password),
            must_change_password=False,
            revoke=True,
        )

    async def _change_user(
        self,
        user_id: uuid.UUID,
        action: AuditAction,
        actor: AuthContext | None,
        client: Client | None,
        *,
        revoke: bool = False,
        **values: object,
    ) -> User:
        now = self.clock.now()
        async with in_transaction(self._sessions, None) as db:
            user = await repo.user_by_id(db, user_id)
            if user is None:
                raise UserNotFound
            await repo.update_user(db, user_id, now, **values)
            if revoke:
                await repo.revoke_user_sessions(db, user_id, now)
            await self._audit_user(action, user, actor, client, db)
            await db.refresh(user)
            return user

    async def _audit_user(
        self,
        action: AuditAction,
        target: User,
        actor: AuthContext | None,
        client: Client | None,
        db: AsyncSession,
    ) -> None:
        await self.audit.record(
            action,
            client,
            user_id=actor.user_id if actor else None,
            email=actor.email if actor else None,
            resource_type="user",
            resource_id=target.user_id,
            details={"email": target.email, **({} if actor else {"via": "console"})},
            db=db,
        )
