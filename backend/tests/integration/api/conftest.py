"""API fixtures: the app with lifespan on the test DB, and a client signed in with a password."""

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.main import create_app
from tests.integration.conftest import _url

ORIGIN = "https://test"
UA = "pytest-browser/1.0"
EMAIL = "owner@example.com"
PASSWORD = "correct horse battery"


def api_settings() -> Settings:
    return get_settings().model_copy(
        update={
            "environment": "test",
            "database_url": _url("test_database_url"),
            "dashboard_origin": ORIGIN,
            "background_enabled": False,
            "discord_token": None,  # never depend on the developer's .env
            "broker_adapter": "fake",  # nor on its Alpaca keys
            "marketdata_adapter": "fake",
        }
    )


@dataclass
class Api:
    app: FastAPI
    client: AsyncClient

    async def add_user(self, email: str = EMAIL) -> str:
        """Allowlist `email` as the console would; returns its temporary password."""
        _, temp = await self.app.state.auth.create_user(email, None, None)
        return str(temp)

    async def login(self, email: str = EMAIL, password: str = PASSWORD) -> httpx.Response:
        return await self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )

    async def sign_in(self, email: str = EMAIL) -> None:
        """Add a user, log in with the temporary password and replace it."""
        temp = await self.add_user(email)
        r = await self.login(email, temp)
        assert r.status_code == 200, r.text
        r = await self.client.post(
            "/api/v1/auth/change-password",
            json={"currentPassword": temp, "newPassword": PASSWORD},
        )
        assert r.status_code == 204, r.text


@pytest.fixture
async def api(owner_engine: object) -> AsyncIterator[Api]:
    """Running app, anonymous client (Origin + User-Agent like the dashboard)."""
    app = create_app(api_settings())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN, "User-Agent": UA},
        ) as client,
    ):
        yield Api(app, client)


@pytest.fixture
async def signed_in(api: Api) -> Api:
    await api.sign_in()
    return api


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[str]:
    """A running app on a real socket (httpx.ASGITransport buffers streamed bodies, e.g. SSE)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:  # noqa: ASYNC110 — uvicorn exposes a flag, not an event
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


async def next_event(response: httpx.Response, name: str) -> dict[str, str]:
    """The `data` of the next SSE event called `name` (1 s budget, F029)."""
    lines = response.aiter_lines()
    async with asyncio.timeout(1):
        while await anext(lines) != f"event: {name}":
            pass
        data = await anext(lines)
    assert data.startswith("data: ")
    parsed: dict[str, str] = json.loads(data[6:])
    return parsed
