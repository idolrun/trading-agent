from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import create_app
from tests.integration.conftest import _url


@pytest.fixture
async def api(owner_engine: object) -> AsyncIterator[AsyncClient]:
    """App with lifespan running against the test database."""
    settings = get_settings().model_copy(
        update={"environment": "test", "database_url": _url("test_database_url")}
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def test_get_before_init_is_404(api: AsyncClient) -> None:
    r = await api.get("/api/v1/config")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


async def test_put_then_get(api: AsyncClient) -> None:
    body = {
        "expectedVersion": 0,
        "reason": "bootstrap",
        "document": {"channels": ["1234567890123456789"], "feed_silence_s": 600},
    }
    r = await api.put("/api/v1/config", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 1
    assert data["document"]["channels"] == ["1234567890123456789"]
    assert "feed_silence_s" not in data["missingForEntries"]
    assert "option_tiers" in data["missingForEntries"]

    r = await api.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json() == data


async def test_stale_version_is_409(api: AsyncClient) -> None:
    body = {"expectedVersion": 0, "reason": "init", "document": {}}
    assert (await api.put("/api/v1/config", json=body)).status_code == 200
    r = await api.put("/api/v1/config", json=body)
    assert r.status_code == 409
    assert r.json()["code"] == "conflict"


async def test_invalid_document_is_422(api: AsyncClient) -> None:
    body = {"expectedVersion": 0, "reason": "x", "document": {"option_tiers": {"default": "0"}}}
    r = await api.put("/api/v1/config", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "invalid"
    assert "option_tiers" in r.json()["message"]


async def test_production_denies_until_passkey_auth(owner_engine: object) -> None:
    settings = get_settings().model_copy(
        update={"environment": "production", "database_url": _url("test_database_url")}
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/api/v1/config")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
