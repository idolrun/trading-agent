"""Shared FastAPI dependencies."""

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import AlertService
from app.api.errors import ApiError
from app.auth.audit import AuditService
from app.auth.service import AuthContext, AuthService, Client
from app.config import Settings
from app.markettime import Clock
from app.runtime_config.service import ConfigService

SESSION_COOKIE = "session"


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_config_service(request: Request) -> ConfigService:
    service: ConfigService = request.app.state.config_service
    return service


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_alerts(request: Request) -> AlertService:
    alerts: AlertService = request.app.state.alerts
    return alerts


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_auth(request: Request) -> AuthService:
    auth: AuthService = request.app.state.auth
    return auth


def get_audit(request: Request) -> AuditService:
    audit: AuditService = request.app.state.audit
    return audit


def get_client(request: Request) -> Client:
    # ponytail: direct peer address; behind a reverse proxy use its forwarded header (F064).
    return Client(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=(request.headers.get("x-request-id") or str(uuid.uuid4()))[:128],
    )


AuthDep = Annotated[AuthService, Depends(get_auth)]
AuditDep = Annotated[AuditService, Depends(get_audit)]
ClientDep = Annotated[Client, Depends(get_client)]


async def require_raw_session(request: Request, auth: AuthDep, client: ClientDep) -> AuthContext:
    ctx = await auth.authenticate(request.cookies.get(SESSION_COOKIE), client.user_agent)
    if ctx is None:
        raise ApiError("sign in with your email and password", "unauthorized", 401)
    return ctx


async def require_session(ctx: Annotated[AuthContext, Depends(require_raw_session)]) -> AuthContext:
    """A signed-in user who has replaced their temporary password."""
    if ctx.must_change_password:
        raise ApiError("change your temporary password first", "password_change_required", 403)
    return ctx


# Session/logout/change-password only: reachable while a password change is still owed.
RawSessionDep = Annotated[AuthContext, Depends(require_raw_session)]
SessionDep = Annotated[AuthContext, Depends(require_session)]


async def require_step_up(ctx: SessionDep, auth: AuthDep) -> AuthContext:
    """Manual entry / flatten: a fresh password check, used once (PRD §13)."""
    if not await auth.consume_step_up(ctx):
        raise ApiError("confirm your password first", "step_up_required", 403)
    return ctx


async def rate_limit(request: Request, auth: AuthDep, client: ClientDep) -> None:
    if not request.app.state.auth_limiter.allow(client.ip or "unknown"):
        await auth.record_rate_limited(client)
        raise ApiError("too many attempts; wait a minute", "rate_limited", 429)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
AlertsDep = Annotated[AlertService, Depends(get_alerts)]
ClockDep = Annotated[Clock, Depends(get_clock)]
ConfigServiceDep = Annotated[ConfigService, Depends(get_config_service)]
StepUpDep = Annotated[AuthContext, Depends(require_step_up)]
