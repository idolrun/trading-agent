from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.alerts.models import Alert
from app.db import create_session_factory
from app.inbox.models import InboxItem
from tests.integration.api.conftest import Api
from tests.messages import ac18, signed


def secret(api: Api) -> str:
    return api.app.state.settings.ingest_hmac_secret.get_secret_value()


async def test_accept_then_duplicate(api: Api, db_session: AsyncSession) -> None:
    body, headers = signed(ac18()["A1"], secret(api))
    first = await api.client.post("/ingest", content=body, headers=headers)
    again = await api.client.post("/ingest", content=body, headers=headers)
    assert (first.status_code, first.json()) == (200, {"accepted": True})
    assert (again.status_code, again.json()) == (200, {"accepted": False})
    assert await db_session.scalar(select(func.count()).select_from(InboxItem)) == 1


async def test_bad_signature_and_stale_timestamp(api: Api, db_session: AsyncSession) -> None:
    body, headers = signed(ac18()["A1"], "wrong-secret")
    assert (await api.client.post("/ingest", content=body, headers=headers)).status_code == 401
    old = datetime.now(UTC) - timedelta(minutes=10)
    body, headers = signed(ac18()["A1"], secret(api), at=old)
    r = await api.client.post("/ingest", content=body, headers=headers)
    assert (r.status_code, r.json()["code"]) == (401, "unauthorized")
    assert await db_session.scalar(select(func.count()).select_from(InboxItem)) == 0


async def test_session_cookie_is_not_auth_for_ingest(signed_in: Api) -> None:
    r = await signed_in.client.post("/ingest", json=ac18()["A1"])
    assert r.status_code == 401


async def test_unmappable_payload_is_422_with_one_alert(api: Api, db_session: AsyncSession) -> None:
    body, headers = signed({"type": "message.pin", "message": {}}, secret(api))
    for _ in range(2):  # redelivery does not create a second alert
        r = await api.client.post("/ingest", content=body, headers=headers)
        assert (r.status_code, r.json()["code"]) == (422, "invalid")
    alerts = (await db_session.scalars(select(Alert))).all()
    assert [(a.kind, a.severity) for a in alerts] == [("message_api", "urgent")]


async def test_database_down_is_503(api: Api) -> None:
    dead = create_async_engine(
        "postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    api.app.state.session_factory = create_session_factory(dead)
    body, headers = signed(ac18()["A1"], secret(api))
    r = await api.client.post("/ingest", content=body, headers=headers)
    assert (r.status_code, r.json()["code"]) == (503, "unavailable")
    await dead.dispose()
