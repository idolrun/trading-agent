from fastapi import APIRouter
from sqlmodel import col, select

from app.api.deps import ConfigServiceDep, SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page, cut, keyset
from app.api.schemas.config import ConfigChangeOut, ConfigResponse, UpdateConfigRequest
from app.runtime_config.models import Actor, ConfigChange
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


@router.get("/changes")
async def list_changes(
    sessions: SessionFactoryDep, _ctx: SessionDep, cursor: Cursor = None, limit: Limit = 50
) -> Page[ConfigChangeOut]:
    query = keyset(
        select(ConfigChange), col(ConfigChange.at), col(ConfigChange.id), cursor, limit, int
    )
    async with sessions() as db:
        rows = list((await db.scalars(query)).all())
    page, nxt = cut(rows, limit, lambda r: (r.at, r.id))
    items = [
        ConfigChangeOut(version=r.version, actor=r.actor, reason=r.reason, diff=r.diff, at=r.at)
        for r in page
    ]
    return Page(items=items, next_cursor=nxt)
