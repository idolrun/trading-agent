from fastapi import APIRouter, Request

from app.api.deps import ClockDep, SessionDep, SessionFactoryDep
from app.api.schemas.health import HealthDetails
from app.system.queries import health_details

router = APIRouter(tags=["health"])


@router.get("/health/details")
async def details(
    request: Request, sessions: SessionFactoryDep, clock: ClockDep, _ctx: SessionDep
) -> HealthDetails:
    async with sessions() as db:
        return await health_details(
            db, feed=getattr(request.app.state, "feed_health", None), now=clock.now()
        )
