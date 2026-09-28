"""F029: SSE requires a session and delivers committed events; push subscription routes."""

import asyncio
import json
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn

from app.db import notify
from tests.integration.api.conftest import ORIGIN, UA, Api


@pytest.fixture
async def served(signed_in: Api) -> AsyncIterator[str]:
    """The same running app on a real socket (ASGITransport buffers streaming bodies)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(signed_in.app, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:  # noqa: ASYNC110 — uvicorn exposes a flag, not an event
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    await task


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
        lines = response.aiter_lines()
        async with asyncio.timeout(1):
            while await anext(lines) != "event: card.created":
                pass
            data = await anext(lines)
        assert data.startswith("data: ") and json.loads(data[6:]) == {"id": "c-1"}


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
