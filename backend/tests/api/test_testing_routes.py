"""F062: the E2E control plane exists only in `environment=test` and stays out of the contract."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.interpret.signal import ParsedSignals
from app.main import create_app
from app.markettime import ControlledClock, SystemClock

ORIGIN = "http://localhost:3000"
AT = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


async def client_for(environment: str) -> AsyncIterator[AsyncClient]:
    settings = get_settings().model_copy(
        update={"environment": environment, "dashboard_origin": ORIGIN}
    )
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Origin": ORIGIN}
    ) as c:
        yield c


@pytest.fixture
async def test_env() -> AsyncIterator[AsyncClient]:
    async for c in client_for("test"):
        yield c


@pytest.mark.parametrize("environment", ["development", "production"])
async def test_routes_do_not_exist_outside_test(environment: str) -> None:
    async for c in client_for(environment):
        app = c._transport.app  # type: ignore[attr-defined]
        assert isinstance(app.state.clock, SystemClock)
        for method, path in [
            ("GET", "/api/v1/test/clock"),
            ("POST", "/api/v1/test/clock"),
            ("POST", "/api/v1/test/reset"),
            ("POST", "/api/v1/test/market"),
            ("PUT", "/api/v1/test/interpretations/1"),
        ]:
            r = await c.request(method, path, json={})
            assert r.status_code == 404, (environment, path)


async def test_clock_is_set_and_advanced_in_test(test_env: AsyncClient) -> None:
    clock = test_env._transport.app.state.clock  # type: ignore[attr-defined]
    assert isinstance(clock, ControlledClock) and clock.at is None  # system time until set
    r = await test_env.post("/api/v1/test/clock", json={"at": AT.isoformat()})
    assert r.status_code == 200 and clock.now() == AT
    r = await test_env.post("/api/v1/test/clock", json={"advanceS": 90})
    assert datetime.fromisoformat(r.json()["now"]) == datetime(2026, 9, 28, 14, 31, 30, tzinfo=UTC)
    assert (await test_env.get("/api/v1/test/clock")).json() == r.json()


async def test_origin_check_still_applies(test_env: AsyncClient) -> None:
    r = await test_env.post("/api/v1/test/clock", json={}, headers={"Origin": "https://evil"})
    assert r.status_code == 403


def test_not_in_the_openapi_contract() -> None:
    settings = get_settings().model_copy(update={"environment": "test"})
    paths = create_app(settings).openapi()["paths"]
    assert not [p for p in paths if p.startswith("/api/v1/test")]


async def test_interpretation_is_validated_and_written(tmp_path: Path) -> None:
    settings = get_settings().model_copy(
        update={
            "environment": "test",
            "dashboard_origin": ORIGIN,
            "interpretation_fixtures_dir": tmp_path,
        }
    )
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Origin": ORIGIN}
    ) as c:
        bad = await c.put("/api/v1/test/interpretations/7", json={"signals": [{"x": 1}]})
        assert bad.status_code == 422
        body = {"signals": [{"msg_type": "trim", "fields": {}}], "interpreter": "llm"}
        assert (await c.put("/api/v1/test/interpretations/7", json=body)).status_code == 204
    assert ParsedSignals.model_validate_json((tmp_path / "7.json").read_text()).signals
