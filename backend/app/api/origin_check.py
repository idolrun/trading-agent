"""CSRF defence in depth (with SameSite=Strict): state-changing API calls must come from the
dashboard origin."""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def origin_check(
    dashboard_origin: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            request.method not in SAFE_METHODS
            and request.url.path.startswith("/api/")
            and request.headers.get("origin") != dashboard_origin
        ):
            return JSONResponse(
                {"message": "cross-origin request refused", "code": "forbidden"}, status_code=403
            )
        return await call_next(request)

    return middleware
