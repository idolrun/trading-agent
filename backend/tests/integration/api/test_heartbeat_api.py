from datetime import UTC, datetime, timedelta

from tests.integration.api.conftest import Api
from tests.integration.api.test_ingest_api import secret
from tests.messages import signed


async def test_heartbeat_counts_as_feed_signal(api: Api) -> None:
    monitor = api.app.state.feed_health
    before = monitor.last_heartbeat_at
    body, headers = signed({}, secret(api))
    r = await api.client.post("/ingest/heartbeat", content=body, headers=headers)
    assert r.status_code == 204
    assert monitor.last_heartbeat_at > before


async def test_heartbeat_requires_signature(api: Api) -> None:
    body, headers = signed({}, "wrong")
    assert (
        await api.client.post("/ingest/heartbeat", content=body, headers=headers)
    ).status_code == 401
    old = datetime.now(UTC) - timedelta(hours=1)
    body, headers = signed({}, secret(api), at=old)
    assert (
        await api.client.post("/ingest/heartbeat", content=body, headers=headers)
    ).status_code == 401
