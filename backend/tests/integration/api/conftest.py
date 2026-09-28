"""API fixtures: the app with lifespan on the test DB, and a client signed in with a passkey."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.main import create_app
from tests.integration.conftest import _url
from tests.webauthn_authenticator import SoftAuthenticator

ORIGIN = "https://test"
UA = "pytest-browser/1.0"


def api_settings() -> Settings:
    return get_settings().model_copy(
        update={
            "environment": "test",
            "database_url": _url("test_database_url"),
            "rp_id": "test",
            "dashboard_origin": ORIGIN,
        }
    )


@dataclass
class Api:
    app: FastAPI
    client: AsyncClient
    authenticator: SoftAuthenticator

    async def enroll(self, label: str = "phone") -> None:
        token, _ = await self.app.state.auth.create_enrollment_token()
        options = await self.client.post(
            "/api/v1/auth/register/options", json={"enrollmentToken": token, "label": label}
        )
        assert options.status_code == 200, options.text
        r = await self.client.post(
            "/api/v1/auth/register/verify",
            json={
                "enrollmentToken": token,
                "credential": self.authenticator.create(options.json()),
            },
        )
        assert r.status_code == 200, r.text

    async def login(self) -> None:
        options = await self.client.post("/api/v1/auth/login/options")
        r = await self.client.post(
            "/api/v1/auth/login/verify",
            json={"credential": self.authenticator.get(options.json())},
        )
        assert r.status_code == 200, r.text


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
        yield Api(app, client, SoftAuthenticator("test", ORIGIN))


@pytest.fixture
async def signed_in(api: Api) -> Api:
    await api.enroll()
    await api.login()
    return api
