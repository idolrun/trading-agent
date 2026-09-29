"""F009: user management and the audit log."""

import uuid

from psycopg import errors
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuditLog, User
from tests.integration.api.conftest import EMAIL, PASSWORD, Api
from tests.integration.schema.helpers import expect_error


async def add(api: Api, email: str) -> dict[str, object]:
    r = await api.client.post("/api/v1/users", json={"email": email})
    assert r.status_code == 201, r.text
    body: dict[str, object] = r.json()
    return body


async def test_users_need_a_session(api: Api) -> None:
    calls = [
        ("get", "/api/v1/users", None),
        ("post", "/api/v1/users", {"email": "a@example.com"}),
        ("post", f"/api/v1/users/{uuid.uuid4()}/deactivate", None),
        ("post", f"/api/v1/users/{uuid.uuid4()}/reactivate", None),
        ("post", f"/api/v1/users/{uuid.uuid4()}/reset-password", None),
        ("get", "/api/v1/audit", None),
    ]
    for method, path, body in calls:
        r = await api.client.request(method, path, json=body)
        assert r.status_code == 401, path
    assert (await api.client.post("/api/v1/auth/register", json={})).status_code == 404


async def test_create_user_returns_temporary_password_once(
    signed_in: Api, db_session: AsyncSession
) -> None:
    body = await add(signed_in, "New@Example.com")
    temp = str(body["temporaryPassword"])
    user = body["user"]
    assert isinstance(user, dict)
    assert user["email"] == "new@example.com" and user["mustChangePassword"] is True
    assert temp not in (await signed_in.client.get("/api/v1/users")).text
    stored = await db_session.scalar(select(User.password_hash).where(User.email == user["email"]))
    assert stored is not None and temp not in stored

    dup = await signed_in.client.post("/api/v1/users", json={"email": "NEW@example.com"})
    assert dup.status_code == 409
    bad = await signed_in.client.post("/api/v1/users", json={"email": "not-an-email"})
    assert bad.status_code == 422

    other = signed_in.client.__class__(
        transport=signed_in.client._transport,
        base_url=signed_in.client.base_url,
        headers=signed_in.client.headers,
    )
    async with other:
        r = await other.post(
            "/api/v1/auth/login", json={"email": "new@example.com", "password": temp}
        )
        assert r.status_code == 200 and r.json()["mustChangePassword"] is True


async def test_deactivate_reactivate_and_reset(signed_in: Api, db_session: AsyncSession) -> None:
    body = await add(signed_in, "b@example.com")
    user = body["user"]
    assert isinstance(user, dict)
    uid, temp = user["userId"], str(body["temporaryPassword"])
    other = signed_in.client.__class__(
        transport=signed_in.client._transport,
        base_url=signed_in.client.base_url,
        headers=signed_in.client.headers,
    )
    async with other:
        assert (
            await other.post(
                "/api/v1/auth/login", json={"email": "b@example.com", "password": temp}
            )
        ).status_code == 200

        r = await signed_in.client.post(f"/api/v1/users/{uid}/deactivate")
        assert r.status_code == 200 and r.json()["isActive"] is False
        assert (await other.get("/api/v1/auth/session")).status_code == 401  # session ended
        r = await other.post(
            "/api/v1/auth/login", json={"email": "b@example.com", "password": temp}
        )
        assert r.status_code == 401

        r = await signed_in.client.post(f"/api/v1/users/{uid}/reactivate")
        assert r.json()["isActive"] is True
        r = await signed_in.client.post(f"/api/v1/users/{uid}/reset-password")
        new_temp = r.json()["temporaryPassword"]
        assert new_temp != temp and r.json()["user"]["mustChangePassword"] is True
        r = await other.post(
            "/api/v1/auth/login", json={"email": "b@example.com", "password": temp}
        )
        assert r.status_code == 401
        r = await other.post(
            "/api/v1/auth/login", json={"email": "b@example.com", "password": new_temp}
        )
        assert r.status_code == 200

    assert (
        await signed_in.client.post(f"/api/v1/users/{uuid.uuid4()}/deactivate")
    ).status_code == 404
    me = next(
        u for u in (await signed_in.client.get("/api/v1/users")).json() if u["email"] == EMAIL
    )
    assert (
        await signed_in.client.post(f"/api/v1/users/{me['userId']}/deactivate")
    ).status_code == 409


async def test_audit_log_records_and_filters(signed_in: Api, db_session: AsyncSession) -> None:
    body = await add(signed_in, "c@example.com")
    r = await signed_in.client.get("/api/v1/audit")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["action"] for i in items][:2] == ["user_created", "password_changed"]
    created = items[0]
    assert created["userEmail"] == EMAIL and created["resourceType"] == "user"
    assert created["details"]["email"] == "c@example.com" and created["requestId"]
    assert "temporaryPassword" not in r.text and str(body["temporaryPassword"]) not in r.text

    only = await signed_in.client.get("/api/v1/audit", params={"action": "login_success"})
    assert [i["action"] for i in only.json()["items"]] == ["login_success"]
    page = await signed_in.client.get("/api/v1/audit", params={"limit": 1})
    assert page.json()["nextCursor"]
    nxt = await signed_in.client.get(
        "/api/v1/audit", params={"limit": 1, "cursor": page.json()["nextCursor"]}
    )
    assert nxt.json()["items"][0]["id"] < page.json()["items"][0]["id"]


async def test_audit_log_is_append_only(signed_in: Api, db_session: AsyncSession) -> None:
    assert await db_session.scalar(select(AuditLog.id)) is not None
    await expect_error(db_session, errors.InsufficientPrivilege, "DELETE FROM audit_log")
    await expect_error(
        db_session, errors.InsufficientPrivilege, "UPDATE audit_log SET action = 'logout'"
    )
    await db_session.execute(text("SELECT 1"))
    assert PASSWORD  # the fixture's password never lands in the log
    dump = (await db_session.scalars(select(AuditLog.details))).all()
    assert PASSWORD not in str(dump)
