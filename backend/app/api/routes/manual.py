"""Manual operations and corrections (F047, PRD 7.9). Thin: the services own the work."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import ValidationError

from app.api.deps import AuditDep, ClientDep, ConfigServiceDep, SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.schemas.ledger import LotDetail, PositionDetail
from app.api.schemas.manual import (
    AddPositionRequest,
    AdjustRequest,
    EditPositionRequest,
    EntryRequest,
    ExitRequest,
    FlattenedLot,
    FlattenOut,
    FlattenRequest,
    KillSwitchOut,
    KillSwitchRequest,
    ManualResultOut,
    ProfileOut,
    ProfileRequest,
    RelinkOut,
    RelinkRequest,
    StopRequest,
)
from app.auth.models import AuditAction
from app.instruments import OptionContract
from app.ledger import queries
from app.manual.corrections import Corrections
from app.manual.service import ManualInvalid, ManualNotFound, ManualResult, ManualService
from app.runtime_config.schema import RuntimeConfig
from app.validate.normalize import normalize_symbol

router = APIRouter(tags=["manual"])


@asynccontextmanager
async def _errors() -> AsyncIterator[None]:
    try:
        yield
    except ManualNotFound as exc:
        raise ApiError(str(exc), "not_found", 404) from None
    except (ManualInvalid, ValidationError) as exc:
        raise ApiError(str(exc), "invalid", 422) from None


def _out(r: ManualResult) -> ManualResultOut:
    return ManualResultOut(card_id=r.card_id, state=r.state, order_ids=r.order_ids)


def _service(request: Request) -> ManualService:
    service: ManualService = request.app.state.manual
    return service


def _corrections(request: Request) -> Corrections:
    corrections: Corrections = request.app.state.manual_corrections
    return corrections


@router.post("/manual/exit")
async def manual_exit(
    body: ExitRequest, request: Request, ctx: SessionDep, audit: AuditDep, client: ClientDep
) -> ManualResultOut:
    async with _errors():
        result = await _service(request).exit(
            body.lot_id, body.qty, body.idempotency_key, ctx.session_id
        )
    await audit.by(
        ctx, client, AuditAction.ORDER_CREATED, "lot", body.lot_id, kind="exit", qty=str(body.qty)
    )
    return _out(result)


@router.post("/manual/stops")
async def manual_stop(
    body: StopRequest, request: Request, ctx: SessionDep, audit: AuditDep, client: ClientDep
) -> ManualResultOut:
    async with _errors():
        result = await _service(request).stop(
            body.lot_id, body.op, body.level, body.basis, body.idempotency_key, ctx.session_id
        )
    action = AuditAction.ORDER_CANCELLED if body.op == "cancel" else AuditAction.ORDER_CREATED
    await audit.by(ctx, client, action, "lot", body.lot_id, kind="stop", op=body.op)
    return _out(result)


@router.post("/manual/entry")
async def manual_entry(
    body: EntryRequest,
    request: Request,
    ctx: SessionDep,
    audit: AuditDep,
    client: ClientDep,
    config: ConfigServiceDep,
) -> ManualResultOut:
    contract = body.instrument.to_instrument()
    if isinstance(contract, OptionContract):  # the signal path's symbol map: SPX -> SPXW
        cfg = config.get() or RuntimeConfig()
        root, _ = normalize_symbol(contract.underlying, cfg.symbol_map, {})
        contract = contract.model_copy(update={"underlying": root})
    async with _errors():
        result = await _service(request).entry(
            contract,
            body.qty,
            body.limit_price,
            body.idempotency_key,
            ctx.session_id,
        )
    await audit.by(
        ctx,
        client,
        AuditAction.ORDER_CREATED,
        "card",
        result.card_id,
        kind="entry",
        qty=str(body.qty),
    )
    return _out(result)


@router.post("/kill-switch")
async def kill_switch(
    body: KillSwitchRequest, request: Request, ctx: SessionDep, audit: AuditDep, client: ClientDep
) -> KillSwitchOut:
    active = await _service(request).kill_switch(body.active, body.reason, ctx.session_id)
    await audit.by(
        ctx,
        client,
        AuditAction.KILL_SWITCH,
        "system_flag",
        "kill_switch",
        active=active,
        reason=body.reason,
    )
    return KillSwitchOut(active=active)


@router.post("/flatten")
async def flatten(
    body: FlattenRequest, request: Request, ctx: SessionDep, audit: AuditDep, client: ClientDep
) -> FlattenOut:
    async with _errors():
        results = await _service(request).flatten(body.scope, body.idempotency_key, ctx.session_id)
    await audit.by(
        ctx, client, AuditAction.ORDER_CREATED, "flatten", None, kind="flatten", lots=len(results)
    )
    return FlattenOut(
        kill_switch=True,
        lots=[
            FlattenedLot(**_out(r).model_dump(by_alias=False), lot_id=r.lot_id, blocked=r.blocked)
            for r in results
        ],
    )


@router.post("/positions")
async def add_position(
    body: AddPositionRequest, request: Request, sessions: SessionFactoryDep, ctx: SessionDep
) -> PositionDetail:
    changes = body.model_dump(
        by_alias=False, exclude={"provider_id", "channel_id", "reason"}, exclude_none=True
    )
    async with _errors():
        position_id = await _corrections(request).add_position(
            body.provider_id, body.channel_id, changes, body.reason, ctx.session_id
        )
    return await _position(sessions, position_id)


@router.patch("/positions/{position_id}")
async def edit_position(
    position_id: uuid.UUID,
    body: EditPositionRequest,
    request: Request,
    sessions: SessionFactoryDep,
    ctx: SessionDep,
) -> PositionDetail:
    changes = body.model_dump(by_alias=False, exclude={"reason"}, exclude_unset=True)
    async with _errors():
        await _corrections(request).edit_position(position_id, changes, body.reason, ctx.session_id)
    return await _position(sessions, position_id)


@router.post("/links")
async def relink(body: RelinkRequest, request: Request, ctx: SessionDep) -> RelinkOut:
    async with _errors():
        created = await _corrections(request).link(
            body.signal_id, body.provider_position_id, body.reason, ctx.session_id
        )
    return RelinkOut(card_ids=created)


@router.post("/lots/{lot_id}/adjust")
async def adjust_lot(
    lot_id: uuid.UUID,
    body: AdjustRequest,
    request: Request,
    sessions: SessionFactoryDep,
    ctx: SessionDep,
    audit: AuditDep,
    client: ClientDep,
) -> LotDetail:
    async with _errors():
        await _service(request).adjust_lot(lot_id, body.qty, body.reason)
    await audit.by(
        ctx, client, AuditAction.LOT_ADJUSTED, "lot", lot_id, qty=str(body.qty), reason=body.reason
    )
    async with sessions() as db:
        lot = await queries.get_lot(db, lot_id)
    assert lot is not None  # noqa: S101 — just adjusted
    return lot


@router.get("/providers/{provider_id}/profile")
async def get_profile(
    provider_id: Annotated[int, Path(gt=0)], request: Request, ctx: SessionDep
) -> ProfileOut:
    return ProfileOut(**await _corrections(request).profile(provider_id))


@router.put("/providers/{provider_id}/profile")
async def put_profile(
    provider_id: Annotated[int, Path(gt=0)], body: ProfileRequest, request: Request, ctx: SessionDep
) -> ProfileOut:
    profile = body.model_dump(by_alias=False, exclude={"reason"}, exclude_none=True)
    async with _errors():
        current = await _corrections(request).set_profile(
            provider_id, profile, body.reason, ctx.session_id
        )
    return ProfileOut(**current)


async def _position(sessions: SessionFactoryDep, position_id: uuid.UUID) -> PositionDetail:
    async with sessions() as db:
        position = await queries.get_position(db, position_id)
    assert position is not None  # noqa: S101 — just written
    return position
