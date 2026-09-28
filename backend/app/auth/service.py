"""Passkey use cases: enrolment, login, sessions, step-up, logout. Every attempt is audited."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from webauthn import base64url_to_bytes
from webauthn.helpers import bytes_to_base64url

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.auth import repo
from app.auth.models import AuthSession, LoginOutcome, PasskeyCredential
from app.auth.sessions import hash_token, new_token, session_problem, step_up_since
from app.auth.webauthn_service import (
    ChallengeStore,
    SignCountRegression,
    VerificationFailed,
    WebAuthn,
)
from app.config import Settings
from app.db import in_transaction
from app.markettime import Clock

logger = logging.getLogger(__name__)

ENROLLMENT_TTL = timedelta(minutes=15)


class InvalidEnrollmentToken(Exception):
    pass


class AuthFailed(Exception):
    pass


@dataclass(frozen=True)
class Client:
    ip: str | None
    user_agent: str | None


@dataclass(frozen=True)
class AuthContext:
    session_id: uuid.UUID
    credential_id: bytes
    expires_at: datetime
    step_up_at: datetime | None

    @classmethod
    def of(cls, row: AuthSession) -> "AuthContext":
        return cls(row.session_id, row.credential_id, row.expires_at, row.step_up_at)


class AuthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock,
        alerts: AlertService,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self.clock = clock
        self._alerts = alerts
        self.webauthn = WebAuthn(settings.rp_id, settings.rp_name, settings.dashboard_origin)
        self.challenges = ChallengeStore(clock)

    # --- Enrolment (console token -> passkey registration) ---

    async def create_enrollment_token(self) -> tuple[str, datetime]:
        """Console only. The raw token is shown once; only its hash is stored."""
        token, expires_at = new_token(), self.clock.now() + ENROLLMENT_TTL
        async with in_transaction(self._sessions, None) as db:
            await repo.add_enrollment_token(db, hash_token(token), expires_at)
        return token, expires_at

    async def registration_options(self, token: str, label: str) -> tuple[dict[str, Any], str]:
        async with in_transaction(self._sessions, None) as db:
            if not await repo.enrollment_token_usable(db, hash_token(token), self.clock.now()):
                raise InvalidEnrollmentToken
            existing = [c.credential_id for c in await repo.active_credentials(db)]
        options, challenge = self.webauthn.registration_options(existing)
        challenge_id = self.challenges.put(
            "register", challenge, token_hash=hash_token(token), label=label
        )
        return options, challenge_id

    async def register(
        self, token: str, credential: dict[str, Any], challenge_id: str | None, client: Client
    ) -> bytes:
        """Raises InvalidEnrollmentToken, or VerificationFailed for a bad attestation."""
        pending = self.challenges.pop(challenge_id, "register")
        if pending is None or pending[1]["token_hash"] != hash_token(token):
            raise InvalidEnrollmentToken
        challenge, data = pending
        try:
            registered = self.webauthn.verify_registration(credential, challenge)
        except VerificationFailed:
            await self._audit(LoginOutcome.FAILURE, client)
            raise
        now = self.clock.now()
        async with in_transaction(self._sessions, None) as db:
            if not await repo.consume_enrollment_token(db, data["token_hash"], now):
                raise InvalidEnrollmentToken
            db.add(
                PasskeyCredential(
                    credential_id=registered.credential_id,
                    public_key=registered.public_key,
                    sign_count=registered.sign_count,
                    label=data["label"],
                )
            )
            await repo.add_login(
                db,
                LoginOutcome.ENROLLED,
                ip=client.ip,
                user_agent=client.user_agent,
                credential_id=registered.credential_id,
            )
        return registered.credential_id

    # --- Login / sessions ---

    async def login_options(self) -> tuple[dict[str, Any], str]:
        async with in_transaction(self._sessions, None) as db:
            allowed = [c.credential_id for c in await repo.active_credentials(db)]
        options, challenge = self.webauthn.authentication_options(allowed)
        return options, self.challenges.put("login", challenge)

    async def login(
        self, credential: dict[str, Any], challenge_id: str | None, client: Client
    ) -> tuple[str, AuthContext]:
        """Returns (cookie token, context). Raises AuthFailed; every attempt is audited."""
        pending = self.challenges.pop(challenge_id, "login")
        stored = await self._verify(credential, pending, client, LoginOutcome.FAILURE)
        token, now = new_token(), self.clock.now()
        row = AuthSession(
            token_hash=hash_token(token),
            credential_id=stored.credential_id,
            expires_at=now + timedelta(hours=self._settings.session_absolute_hours),
            last_seen_at=now,
            ip=client.ip,
            user_agent=client.user_agent,
        )
        async with in_transaction(self._sessions, None) as db:
            db.add(row)
            await db.flush()
            await repo.add_login(
                db,
                LoginOutcome.SUCCESS,
                ip=client.ip,
                user_agent=client.user_agent,
                credential_id=stored.credential_id,
                session_id=row.session_id,
            )
        return token, AuthContext.of(row)

    async def authenticate(self, token: str | None, user_agent: str | None) -> AuthContext | None:
        """Validate a session cookie; revokes idle/expired/device-changed sessions."""
        if not token:
            return None
        now = self.clock.now()
        async with in_transaction(self._sessions, None) as db:
            row = await repo.session_by_token_hash(db, hash_token(token))
            if row is None:
                return None
            problem = session_problem(row, now, user_agent, self._settings)
            if problem is not None:
                await repo.revoke_session(db, row.session_id, now)
                logger.info("session rejected", extra={"reason": problem})
                return None
            await repo.touch_session(db, row.session_id, now)
            return AuthContext.of(row)

    async def logout(self, ctx: AuthContext, client: Client) -> None:
        async with in_transaction(self._sessions, None) as db:
            await repo.revoke_session(db, ctx.session_id, self.clock.now())
            await repo.add_login(
                db,
                LoginOutcome.LOGOUT,
                ip=client.ip,
                user_agent=client.user_agent,
                credential_id=ctx.credential_id,
                session_id=ctx.session_id,
            )

    # --- Step-up (manual entry, flatten) ---

    async def step_up_options(self, ctx: AuthContext) -> tuple[dict[str, Any], str]:
        options, challenge = self.webauthn.authentication_options([ctx.credential_id])
        return options, self.challenges.put("step_up", challenge, session_id=ctx.session_id)

    async def step_up(
        self,
        ctx: AuthContext,
        credential: dict[str, Any],
        challenge_id: str | None,
        client: Client,
    ) -> datetime:
        """Fresh assertion with the session's own passkey. Returns when the step-up lapses."""
        pending = self.challenges.pop(challenge_id, "step_up")
        if pending is not None and pending[1]["session_id"] != ctx.session_id:
            pending = None
        stored = await self._verify(
            credential, pending, client, LoginOutcome.STEP_UP_FAILURE, ctx=ctx
        )
        now = self.clock.now()
        async with in_transaction(self._sessions, None) as db:
            await repo.set_step_up(db, ctx.session_id, now)
            await repo.add_login(
                db,
                LoginOutcome.STEP_UP_SUCCESS,
                ip=client.ip,
                user_agent=client.user_agent,
                credential_id=stored.credential_id,
                session_id=ctx.session_id,
            )
        return now + timedelta(seconds=self._settings.step_up_window_s)

    async def consume_step_up(self, ctx: AuthContext) -> bool:
        """True once per step-up, within the window."""
        since = step_up_since(self.clock.now(), self._settings)
        async with in_transaction(self._sessions, None) as db:
            return await repo.consume_step_up(db, ctx.session_id, since)

    async def record_rate_limited(self, client: Client) -> None:
        await self._audit(LoginOutcome.RATE_LIMITED, client)

    # --- Console ---

    async def credentials(self) -> list[PasskeyCredential]:
        async with in_transaction(self._sessions, None) as db:
            return await repo.all_credentials(db)

    async def revoke(self, credential_id: bytes) -> bool:
        async with in_transaction(self._sessions, None) as db:
            return await repo.revoke_credential(db, credential_id, self.clock.now())

    # --- helpers ---

    async def _verify(
        self,
        credential: dict[str, Any],
        pending: tuple[bytes, dict[str, Any]] | None,
        client: Client,
        failure: LoginOutcome,
        ctx: AuthContext | None = None,
    ) -> PasskeyCredential:
        """Check an assertion against a stored, unrevoked passkey and bump its sign count."""
        credential_id = _raw_id(credential)
        session_id = ctx.session_id if ctx else None
        async with in_transaction(self._sessions, None) as db:
            stored = await repo.active_credential(db, credential_id) if credential_id else None
        if (
            pending is None
            or stored is None
            or (ctx is not None and stored.credential_id != ctx.credential_id)
        ):
            await self._audit(failure, client, credential_id, session_id)
            raise AuthFailed
        try:
            sign_count = self.webauthn.verify_authentication(
                credential, pending[0], stored.public_key, stored.sign_count
            )
        except SignCountRegression:
            await self._alerts.raise_(
                AlertKind.AUTH_ANOMALY,
                Severity.URGENT,
                "Passkey sign count went backwards: possible cloned authenticator",
                details={"credential_id": bytes_to_base64url(stored.credential_id)},
            )
            await self._audit(failure, client, credential_id, session_id)
            raise AuthFailed from None
        except VerificationFailed:
            await self._audit(failure, client, credential_id, session_id)
            raise AuthFailed from None
        async with in_transaction(self._sessions, None) as db:
            await repo.record_credential_use(db, stored.credential_id, sign_count, self.clock.now())
        return stored

    async def _audit(
        self,
        outcome: LoginOutcome,
        client: Client,
        credential_id: bytes | None = None,
        session_id: uuid.UUID | None = None,
    ) -> None:
        async with in_transaction(self._sessions, None) as db:
            await repo.add_login(
                db,
                outcome,
                ip=client.ip,
                user_agent=client.user_agent,
                credential_id=credential_id,
                session_id=session_id,
            )


def _raw_id(credential: dict[str, Any]) -> bytes | None:
    try:
        return base64url_to_bytes(str(credential.get("rawId") or credential["id"]))
    except (KeyError, ValueError):
        return None
