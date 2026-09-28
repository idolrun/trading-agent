from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from webauthn.helpers import bytes_to_base64url

from app.api.deps import (
    SESSION_COOKIE,
    AuthDep,
    ClientDep,
    SessionDep,
    SettingsDep,
    rate_limit,
)
from app.api.errors import ApiError
from app.api.schemas.auth import (
    AssertionRequest,
    RegisterOptionsRequest,
    RegisterVerifyRequest,
    RegisterVerifyResponse,
    SessionResponse,
    StepUpResponse,
)
from app.auth.service import AuthFailed, InvalidEnrollmentToken
from app.auth.webauthn_service import VerificationFailed

router = APIRouter(prefix="/auth", tags=["auth"])
limited = [Depends(rate_limit)]

CHALLENGE_COOKIE = "webauthn_challenge"
CHALLENGE_PATH = "/api/v1/auth"

_INVALID_TOKEN = ApiError("enrolment token is invalid, used or expired", "unauthorized", 401)
_FAILED = ApiError("passkey verification failed", "unauthorized", 401)


def _set_challenge(response: Response, challenge_id: str) -> None:
    response.set_cookie(
        CHALLENGE_COOKIE,
        challenge_id,
        max_age=120,
        path=CHALLENGE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )


def _challenge(request: Request, response: Response) -> str | None:
    response.delete_cookie(CHALLENGE_COOKIE, path=CHALLENGE_PATH)
    return request.cookies.get(CHALLENGE_COOKIE)


@router.post("/register/options", dependencies=limited)
async def register_options(
    body: RegisterOptionsRequest, response: Response, auth: AuthDep
) -> dict[str, Any]:
    try:
        options, challenge_id = await auth.registration_options(body.enrollment_token, body.label)
    except InvalidEnrollmentToken:
        raise _INVALID_TOKEN from None
    _set_challenge(response, challenge_id)
    return options


@router.post("/register/verify", dependencies=limited)
async def register_verify(
    body: RegisterVerifyRequest,
    request: Request,
    response: Response,
    auth: AuthDep,
    client: ClientDep,
) -> RegisterVerifyResponse:
    try:
        credential_id = await auth.register(
            body.enrollment_token, body.credential, _challenge(request, response), client
        )
    except InvalidEnrollmentToken:
        raise _INVALID_TOKEN from None
    except VerificationFailed as exc:
        raise ApiError(f"passkey registration failed: {exc}", "invalid", 422) from None
    return RegisterVerifyResponse(credential_id=bytes_to_base64url(credential_id))


@router.post("/login/options", dependencies=limited)
async def login_options(response: Response, auth: AuthDep) -> dict[str, Any]:
    options, challenge_id = await auth.login_options()
    _set_challenge(response, challenge_id)
    return options


@router.post("/login/verify", dependencies=limited)
async def login_verify(
    body: AssertionRequest,
    request: Request,
    response: Response,
    auth: AuthDep,
    client: ClientDep,
    settings: SettingsDep,
) -> SessionResponse:
    try:
        token, ctx = await auth.login(body.credential, _challenge(request, response), client)
    except AuthFailed:
        raise _FAILED from None
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return SessionResponse(expires_at=ctx.expires_at)


@router.post("/step-up/options", dependencies=limited)
async def step_up_options(ctx: SessionDep, response: Response, auth: AuthDep) -> dict[str, Any]:
    options, challenge_id = await auth.step_up_options(ctx)
    _set_challenge(response, challenge_id)
    return options


@router.post("/step-up/verify", dependencies=limited)
async def step_up_verify(
    body: AssertionRequest,
    ctx: SessionDep,
    request: Request,
    response: Response,
    auth: AuthDep,
    client: ClientDep,
) -> StepUpResponse:
    try:
        until = await auth.step_up(ctx, body.credential, _challenge(request, response), client)
    except AuthFailed:
        raise _FAILED from None
    return StepUpResponse(step_up_until=until)


@router.post("/logout", status_code=204)
async def logout(ctx: SessionDep, response: Response, auth: AuthDep, client: ClientDep) -> None:
    await auth.logout(ctx, client)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/session")
async def session(ctx: SessionDep, settings: SettingsDep) -> SessionResponse:
    step_up_until = None
    if ctx.step_up_at is not None:
        step_up_until = ctx.step_up_at + timedelta(seconds=settings.step_up_window_s)
    return SessionResponse(expires_at=ctx.expires_at, step_up_until=step_up_until)
