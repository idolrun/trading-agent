"""F062: POST /api/v1/test/reset empties the tables under a running app and seeds the config."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.auth.models import User
from tests.decide_helpers import CONFIG
from tests.integration.api.conftest import Api
from tests.integration.conftest import _url

AT = datetime(2026, 9, 28, 14, 30, 5, tzinfo=UTC)


async def users(api: Api) -> int:
    async with api.app.state.session_factory() as db:
        return int(await db.scalar(select(func.count()).select_from(User)) or 0)


async def test_reset_refuses_an_owner_url_for_another_database(signed_in: Api) -> None:
    """`api_settings()` points only DATABASE_URL at trading_test: never truncate the other DB."""
    assert (await signed_in.client.post("/api/v1/test/reset", json={})).status_code == 409


async def test_reset_keeps_users_ends_sessions_and_seeds_config(signed_in: Api) -> None:
    api = signed_in
    api.app.state.settings = api.app.state.settings.model_copy(
        update={"database_owner_url": _url("test_database_owner_url")}
    )
    api.app.state.marketdata.set_underlying("GOOGL", 1)
    api.app.state.simulated.calls.append(("submit", None))
    r = await api.client.post(
        "/api/v1/test/reset",
        json={"at": AT.isoformat(), "config": CONFIG.model_dump(mode="json")},
    )
    assert r.status_code == 204, r.text
    assert api.app.state.clock.now() == AT
    assert api.app.state.config_service.snapshot() == (1, CONFIG)
    assert api.app.state.marketdata.underlyings == {}
    assert api.app.state.simulated.calls == []  # deterministic client order ids repeat per test
    assert await users(api) == 1
    assert (await api.client.get("/api/v1/cards")).status_code == 401  # sessions are gone

    r = await api.client.post("/api/v1/test/reset", json={"keepUsers": False})
    assert r.status_code == 204 and await users(api) == 0
    assert api.app.state.config_service.get() is None
