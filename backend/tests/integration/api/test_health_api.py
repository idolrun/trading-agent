"""`/health` (TRD §2.4): database reachable and, with background services, heartbeat <= 15 s."""

from datetime import timedelta

import pytest

from app.db import in_transaction
from app.system.heartbeat import beat
from tests.integration.api.conftest import Api


async def write_beat(api: Api, age: timedelta) -> None:
    async with in_transaction(api.app.state.session_factory, None) as db:
        await beat(db, api.app.state.clock.now() - age)


@pytest.fixture
def background(api: Api) -> Api:
    """The app as if its background services ran (the tests write the heartbeat themselves)."""
    api.app.state.settings = api.app.state.settings.model_copy(update={"background_enabled": True})
    return api


async def test_fresh_heartbeat_is_healthy(background: Api) -> None:
    await write_beat(background, timedelta(seconds=3))
    r = await background.client.get("/health")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})


async def test_stale_heartbeat_fails(background: Api) -> None:
    await write_beat(background, timedelta(seconds=20))
    r = await background.client.get("/health")
    assert (r.status_code, r.json()["reason"]) == (503, "dispatcher heartbeat stale")


async def test_missing_heartbeat_fails(background: Api) -> None:
    assert (await background.client.get("/health")).status_code == 503


async def test_heartbeat_is_not_required_without_background_services(api: Api) -> None:
    await write_beat(api, timedelta(minutes=5))  # ignored
    assert (await api.client.get("/health")).status_code == 200
