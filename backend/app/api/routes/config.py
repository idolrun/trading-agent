from fastapi import APIRouter

from app.api.deps import ConfigServiceDep, SessionIdDep
from app.api.errors import ApiError
from app.api.schemas.config import ConfigResponse, UpdateConfigRequest
from app.runtime_config.models import Actor
from app.runtime_config.repo import VersionConflict

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config(service: ConfigServiceDep, _session_id: SessionIdDep) -> ConfigResponse:
    snapshot = service.snapshot()
    if snapshot is None:
        raise ApiError("runtime config has not been initialized", "not_found", 404)
    return ConfigResponse.of(*snapshot)


@router.put("")
async def put_config(
    body: UpdateConfigRequest, service: ConfigServiceDep, session_id: SessionIdDep
) -> ConfigResponse:
    try:
        snapshot = await service.update(
            body.document,
            expected_version=body.expected_version,
            actor=Actor.USER,
            reason=body.reason,
            session_id=session_id,
        )
    except VersionConflict:
        raise ApiError(
            "config changed since expectedVersion; reload and retry", "conflict", 409
        ) from None
    return ConfigResponse.of(*snapshot)
