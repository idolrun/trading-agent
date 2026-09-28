from fastapi import APIRouter

from app.api.deps import ConfigServiceDep, SessionDep
from app.api.errors import ApiError
from app.api.schemas.config import ConfigResponse, UpdateConfigRequest
from app.runtime_config.models import Actor
from app.runtime_config.repo import VersionConflict
from app.runtime_config.service import ConfigInvalid

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config(service: ConfigServiceDep, _ctx: SessionDep) -> ConfigResponse:
    snapshot = service.snapshot()
    if snapshot is None:
        raise ApiError("runtime config has not been initialized", "not_found", 404)
    return ConfigResponse.of(*snapshot)


@router.put("")
async def put_config(
    body: UpdateConfigRequest, service: ConfigServiceDep, ctx: SessionDep
) -> ConfigResponse:
    try:
        snapshot = await service.update(
            body.document,
            expected_version=body.expected_version,
            actor=Actor.USER,
            reason=body.reason,
            session_id=ctx.session_id,
        )
    except VersionConflict:
        raise ApiError(
            "config changed since expectedVersion; reload and retry", "conflict", 409
        ) from None
    except ConfigInvalid as exc:
        raise ApiError(str(exc), "invalid", 422) from None
    return ConfigResponse.of(*snapshot)
