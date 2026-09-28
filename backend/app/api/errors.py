"""API error type and handler: every error body is `{"message": str, "code": str}`."""

from fastapi import Request
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
