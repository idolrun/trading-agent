from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import SessionDep
from app.api.schemas.health import HealthDetails, Window
from app.observability.service import HealthService

router = APIRouter(tags=["health"])
liveness = APIRouter(tags=["health"])  # mounted without the /api/v1 prefix


@router.get("/health/details")
async def details(request: Request, _ctx: SessionDep, window: Window = "day") -> HealthDetails:
    """PRD §16 metrics over `window`: `session` (since process start), `day` (24 h, default)
    or `7d`. In-process counters (`*_since_start`) ignore the window."""
    health: HealthService = request.app.state.health
    return await health.details(window)


@liveness.get("/health", responses={503: {"description": "Database down or heartbeat stale"}})
async def health(request: Request) -> JSONResponse:
    """External uptime monitor target (TRD §2.4): 503 when the DB is unreachable or, with
    background services running, the dispatcher heartbeat is older than 15 s."""
    service: HealthService | None = getattr(request.app.state, "health", None)
    reason: str | None = "not started"
    if service is not None:
        reason = await service.alive(
            require_heartbeat=request.app.state.settings.background_enabled
        )
    if reason is None:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "unhealthy", "reason": reason}, status_code=503)
