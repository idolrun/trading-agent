"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Request

from app.api.errors import ApiError
from app.auth.service import AuthContext, AuthService, Client
from app.config import Settings
from app.runtime_config.service import ConfigService

SESSION_COOKIE = "session"


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_config_service(request: Request) -> ConfigService:
    service: ConfigService = request.app.state.config_service
    return service


def get_auth(request: Request) -> AuthService:
    auth: AuthService = request.app.state.auth
    return auth


def get_client(request: Request) -> Client:
    # ponytail: direct peer address; behind a reverse proxy use its forwarded header (F064).
    return Client(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


AuthDep = Annotated[AuthService, Depends(get_auth)]
ClientDep = Annotated[Client, Depends(get_client)]


async def require_session(request: Request, auth: AuthDep, client: ClientDep) -> AuthContext:
    ctx = await auth.authenticate(request.cookies.get(SESSION_COOKIE), client.user_agent)
    if ctx is None:
        raise ApiError("sign in with your passkey", "unauthorized", 401)
    return ctx


SessionDep = Annotated[AuthContext, Depends(require_session)]


async def require_step_up(ctx: SessionDep, auth: AuthDep) -> AuthContext:
    """Manual entry / flatten: a fresh passkey assertion, used once (PRD §13)."""
    if not await auth.consume_step_up(ctx):
        raise ApiError("confirm with your passkey first", "step_up_required", 403)
    return ctx


async def rate_limit(request: Request, auth: AuthDep, client: ClientDep) -> None:
    if not request.app.state.auth_limiter.allow(client.ip or "unknown"):
        await auth.record_rate_limited(client)
        raise ApiError("too many attempts; wait a minute", "rate_limited", 429)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ConfigServiceDep = Annotated[ConfigService, Depends(get_config_service)]
StepUpDep = Annotated[AuthContext, Depends(require_step_up)]
