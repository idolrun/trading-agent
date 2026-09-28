"""F029: SSE requires a session and delivers committed events; push subscription routes."""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.db import notify
from tests.integration.api.conftest import ORIGIN, UA, Api, next_event, serve


@pytest.fixture
async def served(signed_in: Api) -> AsyncIterator[str]:
    async with serve(signed_in.app) as url:
        yield url


async def test_events_require_a_session(api: Api) -> None:
    r = await api.client.get("/api/v1/events")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


async def test_committed_card_event_is_streamed(signed_in: Api, served: str) -> None:
    cookies = dict(signed_in.client.cookies)
    headers = {"Origin": ORIGIN, "User-Agent": UA}
    async with (
        httpx.AsyncClient(base_url=served, cookies=cookies, headers=headers) as client,
        client.stream("GET", "/api/v1/events") as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        await asyncio.sleep(0.3)  # bus listener + subscription ready
        async with signed_in.app.state.session_factory() as db:
            await notify(db, "cards", json.dumps({"type": "card.created", "id": "c-1"}))
            await db.commit()
        assert await next_event(response, "card.created") == {"id": "c-1"}


async def test_push_subscription_lifecycle(signed_in: Api) -> None:
    c = signed_in.client
    assert (await c.get("/api/v1/push/vapid-public-key")).status_code == 503  # not configured
    body = {"endpoint": "https://push.example/1", "keys": {"p256dh": "k", "auth": "a"}}
    first = await c.post("/api/v1/push/subscriptions", json=body)
    again = await c.post("/api/v1/push/subscriptions", json=body)
    assert first.status_code == again.status_code == 201
    sub_id = first.json()["subscriptionId"]
    assert again.json()["subscriptionId"] == sub_id  # idempotent on the endpoint
    bad = await c.post("/api/v1/push/subscriptions", json={**body, "endpoint": "http://x"})
    assert bad.status_code == 422
    assert (await c.delete(f"/api/v1/push/subscriptions/{sub_id}")).status_code == 204
    assert (await c.delete(f"/api/v1/push/subscriptions/{sub_id}")).status_code == 404
