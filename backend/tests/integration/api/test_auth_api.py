"""F009: email + password auth end to end."""

from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import StepUpDep
from app.auth.models import AuditLog, AuthSession, User
from app.auth.sessions import hash_token
from app.markettime import FixedClock
from tests.integration.api.conftest import EMAIL, PASSWORD, Api


async def actions(db: AsyncSession) -> list[str]:
    return list((await db.scalars(select(AuditLog.action).order_by(AuditLog.id))).all())


async def test_login_session_logout(api: Api, db_session: AsyncSession) -> None:
    assert (await api.client.get("/api/v1/auth/session")).status_code == 401
    await api.sign_in()
    r = await api.client.get("/api/v1/auth/session")
    assert r.status_code == 200
    assert r.json()["email"] == EMAIL
    assert r.json()["stepUpUntil"] is None and r.json()["mustChangePassword"] is False
    assert (await api.client.post("/api/v1/auth/logout")).status_code == 204
    assert (await api.client.get("/api/v1/auth/session")).status_code == 401
    assert await actions(db_session) == [
        "user_created",
        "login_success",
        "password_changed",
        "logout",
    ]


async def test_session_cookie_flags(api: Api) -> None:
    await api.add_user()
    api.client.cookies.clear()
    temp = await api.add_user("other@example.com")
    r = await api.login("other@example.com", temp)
    cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith("session="))
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert flag.lower() in cookie.lower()


async def test_login_failures_look_alike_and_are_audited(
    api: Api, db_session: AsyncSession
) -> None:
    temp = await api.add_user()
    inactive = await api.add_user("off@example.com")
    off = await db_session.scalar(select(User).where(User.email == "off@example.com"))
    assert off is not None
    await db_session.execute(
        text("UPDATE users SET is_active = false WHERE user_id = :u"), {"u": off.user_id}
    )
    await db_session.commit()
    bodies = []
    for email, password in [
        (EMAIL, "wrong password!!"),
        ("nobody@example.com", temp),
        ("off@example.com", inactive),
    ]:
        r = await api.login(email, password)
        assert r.status_code == 401
        bodies.append(r.json())
    assert bodies[0] == bodies[1] == bodies[2]
    assert (await actions(db_session))[2:] == ["login_failed"] * 3
    assert await db_session.scalar(select(AuthSession.session_id)) is None
    users = (await db_session.scalars(select(User))).all()
    assert len(users) == 2  # an unknown email never creates an account


async def test_email_is_case_insensitive(api: Api) -> None:
    temp = await api.add_user("Owner@Example.com")
    assert (await api.login("OWNER@example.COM", temp)).status_code == 200


async def test_temporary_password_must_be_changed(api: Api) -> None:
    temp = await api.add_user()
    assert (await api.login(EMAIL, temp)).status_code == 200
    session = await api.client.get("/api/v1/auth/session")
    assert session.json()["mustChangePassword"] is True
    r = await api.client.get("/api/v1/alerts")
    assert (r.status_code, r.json()["code"]) == (403, "password_change_required")

    change = "/api/v1/auth/change-password"
    r = await api.client.post(change, json={"currentPassword": "nope", "newPassword": PASSWORD})
    assert r.status_code == 401
    r = await api.client.post(change, json={"currentPassword": temp, "newPassword": "short"})
    assert (r.status_code, r.json()["code"]) == (422, "invalid")
    r = await api.client.post(change, json={"currentPassword": temp, "newPassword": temp})
    assert r.status_code == 422
    r = await api.client.post(change, json={"currentPassword": temp, "newPassword": PASSWORD})
    assert r.status_code == 204
    assert (await api.client.get("/api/v1/alerts")).status_code == 200
    api.client.cookies.clear()
    assert (await api.login(EMAIL, temp)).status_code == 401
    assert (await api.login(EMAIL, PASSWORD)).status_code == 200


async def test_change_password_ends_other_sessions(signed_in: Api) -> None:
    other = signed_in.client.__class__(
        transport=signed_in.client._transport,
        base_url=signed_in.client.base_url,
        headers=signed_in.client.headers,
    )
    async with other:
        r = await other.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert r.status_code == 200
        r = await signed_in.client.post(
            "/api/v1/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": "another long password"},
        )
        assert r.status_code == 204
        assert (await signed_in.client.get("/api/v1/auth/session")).status_code == 200
        assert (await other.get("/api/v1/auth/session")).status_code == 401


async def test_user_agent_change_revokes_session(signed_in: Api) -> None:
    r = await signed_in.client.get(
        "/api/v1/auth/session", headers={"User-Agent": "other-device/2.0"}
    )
    assert r.status_code == 401
    assert (await signed_in.client.get("/api/v1/auth/session")).status_code == 401


async def test_idle_session_expires(signed_in: Api) -> None:
    auth = signed_in.app.state.auth
    auth.clock = FixedClock(auth.clock.now() + timedelta(minutes=61))
    assert (await signed_in.client.get("/api/v1/auth/session")).status_code == 401


def _add_step_up_route(app: FastAPI) -> None:
    async def flatten(_ctx: StepUpDep) -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/api/v1/_test/flatten", flatten, methods=["POST"])


async def test_step_up_required_and_single_use(signed_in: Api, db_session: AsyncSession) -> None:
    _add_step_up_route(signed_in.app)
    client = signed_in.client

    r = await client.post("/api/v1/_test/flatten")
    assert (r.status_code, r.json()["code"]) == (403, "step_up_required")

    r = await client.post("/api/v1/auth/step-up", json={"password": "wrong password"})
    assert r.status_code == 401
    assert (await client.post("/api/v1/_test/flatten")).status_code == 403  # a failure grants none

    r = await client.post("/api/v1/auth/step-up", json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["stepUpUntil"]
    assert (await client.post("/api/v1/_test/flatten")).status_code == 200
    assert (await client.post("/api/v1/_test/flatten")).status_code == 403  # used up
    recorded = await actions(db_session)
    assert "step_up_failure" in recorded and recorded[-1] == "step_up_success"


async def test_step_up_window_lapses(signed_in: Api) -> None:
    _add_step_up_route(signed_in.app)
    await signed_in.step_up()
    auth = signed_in.app.state.auth
    auth.clock = FixedClock(auth.clock.now() + timedelta(seconds=121))
    assert (await signed_in.client.post("/api/v1/_test/flatten")).status_code == 403


async def test_rate_limit(api: Api, db_session: AsyncSession) -> None:
    body = {"email": EMAIL, "password": "x"}
    codes = [
        (await api.client.post("/api/v1/auth/login", json=body)).status_code for _ in range(11)
    ]
    assert codes == [401] * 10 + [429]
    assert (await actions(db_session))[-1] == "rate_limited"


async def test_session_token_is_stored_hashed(signed_in: Api, db_session: AsyncSession) -> None:
    token = signed_in.client.cookies["session"]
    stored = (await db_session.scalars(select(AuthSession.token_hash))).all()
    assert stored == [hash_token(token)]


async def test_password_is_stored_hashed(signed_in: Api, db_session: AsyncSession) -> None:
    hashes = (await db_session.scalars(select(User.password_hash))).all()
    assert len(hashes) == 1 and hashes[0].startswith("scrypt$") and PASSWORD not in hashes[0]
