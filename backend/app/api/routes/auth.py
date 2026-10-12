from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    SESSION_COOKIE,
    AuthDep,
    ClientDep,
    RawSessionDep,
    SettingsDep,
    rate_limit,
)
from app.api.errors import ApiError
from app.api.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    SessionResponse,
)
from app.auth.service import AuthContext, AuthFailed, InvalidPassword

router = APIRouter(prefix="/auth", tags=["auth"])
limited = [Depends(rate_limit)]

_FAILED = ApiError("wrong email or password", "unauthorized", 401)


def _session(ctx: AuthContext, settings: SettingsDep) -> SessionResponse:
    return SessionResponse(
        email=ctx.email,
        expires_at=ctx.expires_at,
        must_change_password=ctx.must_change_password,
    )


@router.post("/login", dependencies=limited)
async def login(
    body: LoginRequest,
    response: Response,
    auth: AuthDep,
    client: ClientDep,
    settings: SettingsDep,
) -> SessionResponse:
    try:
        token, ctx = await auth.login(body.email, body.password, client)
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
    return _session(ctx, settings)


@router.post("/change-password", status_code=204, dependencies=limited)
async def change_password(
    body: ChangePasswordRequest, ctx: RawSessionDep, auth: AuthDep, client: ClientDep
) -> None:
    try:
        await auth.change_password(ctx, body.current_password, body.new_password, client)
    except AuthFailed:
        raise ApiError("current password is wrong", "unauthorized", 401) from None
    except InvalidPassword as exc:
        raise ApiError(str(exc), "invalid", 422) from None


@router.post("/logout", status_code=204)
async def logout(ctx: RawSessionDep, response: Response, auth: AuthDep, client: ClientDep) -> None:
    await auth.logout(ctx, client)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/session")
async def session(ctx: RawSessionDep, settings: SettingsDep) -> SessionResponse:
    return _session(ctx, settings)
