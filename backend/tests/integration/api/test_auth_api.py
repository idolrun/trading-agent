"""F009: passkey auth end to end with a software authenticator."""

from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.models import Alert
from app.api.deps import StepUpDep
from app.auth.models import AuthSession, Login
from app.auth.sessions import hash_token
from app.markettime import FixedClock
from tests.integration.api.conftest import ORIGIN, Api
from tests.webauthn_authenticator import SoftAuthenticator


async def outcomes(db: AsyncSession) -> list[str]:
    return list((await db.scalars(select(Login.outcome).order_by(Login.id))).all())


async def test_register_login_session_logout(api: Api, db_session: AsyncSession) -> None:
    assert (await api.client.get("/api/v1/auth/session")).status_code == 401
    await api.enroll()
    await api.login()
    r = await api.client.get("/api/v1/auth/session")
    assert r.status_code == 200
    assert r.json()["stepUpUntil"] is None
    assert "session" in api.client.cookies
    assert (await api.client.post("/api/v1/auth/logout")).status_code == 204
    assert (await api.client.get("/api/v1/auth/session")).status_code == 401
    assert await outcomes(db_session) == ["enrolled", "success", "logout"]


async def test_session_cookie_flags(api: Api) -> None:
    await api.enroll()
    options = await api.client.post("/api/v1/auth/login/options")
    r = await api.client.post(
        "/api/v1/auth/login/verify", json={"credential": api.authenticator.get(options.json())}
    )
    cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith("session="))
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert flag.lower() in cookie.lower()


async def test_enrollment_token_single_use_and_expiry(api: Api, db_session: AsyncSession) -> None:
    await api.enroll()
    auth = api.app.state.auth
    # expired token
    auth.clock = FixedClock(auth.clock.now() - timedelta(hours=1))
    token, _ = await auth.create_enrollment_token()
    auth.clock = api.app.state.clock
    r = await api.client.post(
        "/api/v1/auth/register/options", json={"enrollmentToken": token, "label": "old"}
    )
    assert r.status_code == 401
    # unknown token
    r = await api.client.post(
        "/api/v1/auth/register/options", json={"enrollmentToken": "nope", "label": "x"}
    )
    assert r.status_code == 401


async def test_used_enrollment_token_cannot_register_again(api: Api) -> None:
    token, _ = await api.app.state.auth.create_enrollment_token()
    body = {"enrollmentToken": token, "label": "phone"}
    options = await api.client.post("/api/v1/auth/register/options", json=body)
    r = await api.client.post(
        "/api/v1/auth/register/verify",
        json={"enrollmentToken": token, "credential": api.authenticator.create(options.json())},
    )
    assert r.status_code == 200
    r = await api.client.post("/api/v1/auth/register/options", json=body)
    assert r.status_code == 401


async def test_wrong_origin_rejected(api: Api) -> None:
    r = await api.client.post(
        "/api/v1/auth/login/options", headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    r = await api.client.post("/api/v1/auth/login/options", headers={"Origin": ""})
    assert r.status_code == 403


async def test_unknown_passkey_fails_and_is_audited(api: Api, db_session: AsyncSession) -> None:
    await api.enroll()
    stranger = SoftAuthenticator("test", ORIGIN)
    options = await api.client.post("/api/v1/auth/login/options")
    r = await api.client.post(
        "/api/v1/auth/login/verify", json={"credential": stranger.get(options.json())}
    )
    assert r.status_code == 401
    assert (await outcomes(db_session))[-1] == "failure"


async def test_challenge_is_single_use(api: Api) -> None:
    await api.enroll()
    options = await api.client.post("/api/v1/auth/login/options")
    challenge_cookie = api.client.cookies.get("webauthn_challenge")
    await api.client.post(
        "/api/v1/auth/login/verify", json={"credential": api.authenticator.get(options.json())}
    )
    api.client.cookies.set("webauthn_challenge", challenge_cookie or "", path="/api/v1/auth")
    r = await api.client.post(
        "/api/v1/auth/login/verify", json={"credential": api.authenticator.get(options.json())}
    )
    assert r.status_code == 401


async def test_sign_count_regression_raises_urgent_alert(
    api: Api, db_session: AsyncSession
) -> None:
    await api.enroll()
    await api.login()
    api.authenticator.sign_count = 0  # a clone would replay an older counter
    options = await api.client.post("/api/v1/auth/login/options")
    r = await api.client.post(
        "/api/v1/auth/login/verify", json={"credential": api.authenticator.get(options.json())}
    )
    assert r.status_code == 401
    alert = await db_session.scalar(select(Alert))
    assert alert is not None
    assert (alert.kind, alert.severity) == ("auth_anomaly", "urgent")


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


async def test_step_up_required_and_single_use(signed_in: Api, db_session: AsyncSession) -> None:
    app: FastAPI = signed_in.app

    async def flatten(_ctx: StepUpDep) -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/api/v1/_test/flatten", flatten, methods=["POST"])
    client = signed_in.client

    r = await client.post("/api/v1/_test/flatten")
    assert (r.status_code, r.json()["code"]) == (403, "step_up_required")

    options = await client.post("/api/v1/auth/step-up/options")
    r = await client.post(
        "/api/v1/auth/step-up/verify",
        json={"credential": signed_in.authenticator.get(options.json())},
    )
    assert r.status_code == 200 and r.json()["stepUpUntil"]
    assert (await client.post("/api/v1/_test/flatten")).status_code == 200
    assert (await client.post("/api/v1/_test/flatten")).status_code == 403  # used up
    assert (await outcomes(db_session))[-1] == "step_up_success"


async def test_step_up_window_lapses(signed_in: Api) -> None:
    app: FastAPI = signed_in.app

    async def flatten(_ctx: StepUpDep) -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/api/v1/_test/flatten", flatten, methods=["POST"])
    options = await signed_in.client.post("/api/v1/auth/step-up/options")
    await signed_in.client.post(
        "/api/v1/auth/step-up/verify",
        json={"credential": signed_in.authenticator.get(options.json())},
    )
    auth = app.state.auth
    auth.clock = FixedClock(auth.clock.now() + timedelta(seconds=121))
    assert (await signed_in.client.post("/api/v1/_test/flatten")).status_code == 403


async def test_rate_limit(api: Api, db_session: AsyncSession) -> None:
    codes = [(await api.client.post("/api/v1/auth/login/options")).status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]
    assert (await outcomes(db_session))[-1] == "rate_limited"


async def test_session_token_is_stored_hashed(signed_in: Api, db_session: AsyncSession) -> None:
    token = signed_in.client.cookies["session"]
    stored = (await db_session.scalars(select(AuthSession.token_hash))).all()
    assert stored == [hash_token(token)]
