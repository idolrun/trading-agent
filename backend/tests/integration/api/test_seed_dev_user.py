"""`app.auth.cli seed-dev-user`: the local desk login, development only."""

import argparse

import pytest

from app.auth import cli
from tests.integration.api.conftest import Api, api_settings


def settings_for(environment: str) -> object:
    return api_settings().model_copy(update={"environment": environment})


async def test_seed_dev_user_signs_in_without_a_forced_change(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings_for("development"))
    seed = argparse.Namespace(command="seed-dev-user")
    assert await cli.run(seed) == 0
    assert await cli.run(seed) == 0  # re-runnable: resets the password, no second user

    r = await api.login(cli.DEV_EMAIL, cli.DEV_PASSWORD)
    assert r.status_code == 200, r.text
    session = (await api.client.get("/api/v1/auth/session")).json()
    assert session["mustChangePassword"] is False
    assert [u.email for u in await api.app.state.auth.users()] == [cli.DEV_EMAIL]


@pytest.mark.parametrize("environment", ["test", "production"])
async def test_seed_dev_user_is_refused_outside_development(
    api: Api, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings_for(environment))
    assert await cli.run(argparse.Namespace(command="seed-dev-user")) == 1
    assert await api.app.state.auth.users() == []
