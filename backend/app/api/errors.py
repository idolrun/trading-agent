"""API error type and handlers: every error body is `{"message": str, "code": str}`."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, message: str, code: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 — registered only for ApiError
    return JSONResponse({"message": exc.message, "code": exc.code}, status_code=exc.status)


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 — registered only for it
    message = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
    )
    return JSONResponse({"message": message, "code": "invalid"}, status_code=422)
