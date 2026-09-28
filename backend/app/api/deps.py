"""Shared FastAPI dependencies."""

import uuid
from typing import Annotated

from fastapi import Depends, Request

from app.api.errors import ApiError
from app.config import Settings
from app.runtime_config.service import ConfigService


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_config_service(request: Request) -> ConfigService:
    service: ConfigService = request.app.state.config_service
    return service


async def require_session(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> uuid.UUID | None:
    """Placeholder until passkey sessions (F009): open in development/test, closed in production.

    Returns the session id for audit rows (None until F009).
    """
    if settings.environment == "production":
        raise ApiError("authentication required", "unauthorized", 401)
    return None


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ConfigServiceDep = Annotated[ConfigService, Depends(get_config_service)]
SessionIdDep = Annotated[uuid.UUID | None, Depends(require_session)]
