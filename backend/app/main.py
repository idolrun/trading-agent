"""FastAPI app factory. Background services are started in `lifespan`."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import make_url, text

from app.alerts.service import AlertService
from app.api.errors import ApiError, api_error_handler, validation_error_handler
from app.api.routes import config as config_routes
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.logging import configure_logging
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService

logger = logging.getLogger(__name__)


def libpq_dsn(sqlalchemy_url: str) -> str:
    """`postgresql+psycopg://…` → `postgresql://…` for a raw psycopg connection (LISTEN)."""
    return make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("database unreachable at startup")
        await engine.dispose()
        raise
    session_factory = create_session_factory(engine)
    config_service = ConfigService(session_factory, libpq_dsn(str(settings.database_url)))
    await config_service.load()
    config_listener = asyncio.create_task(config_service.listen(), name="config-listener")

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.config_service = config_service
    app.state.alerts = AlertService(session_factory)
    app.state.flags = FlagService(session_factory, app.state.alerts)
    try:
        yield
    finally:
        config_listener.cancel()
        with suppress(asyncio.CancelledError):
            await config_listener
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Trading Agent", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(config_routes.router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
