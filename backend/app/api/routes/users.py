"""User management and audit history. Any signed-in user has the same access: no roles."""

import uuid

from fastapi import APIRouter

from app.api.deps import AuditDep, AuthDep, ClientDep, SessionDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.users import AuditEntry, CreateUserRequest, UserOut, UserWithPassword
from app.auth.models import AuditAction
from app.auth.service import CannotDeactivateSelf, UserExists, UserNotFound

router = APIRouter(tags=["users"])

_NOT_FOUND = ApiError("user not found", "not_found", 404)


@router.get("/users")
async def list_users(auth: AuthDep, _ctx: SessionDep) -> list[UserOut]:
    return [UserOut.model_validate(u, from_attributes=True) for u in await auth.users()]


@router.post("/users", status_code=201)
async def create_user(
    body: CreateUserRequest, auth: AuthDep, ctx: SessionDep, client: ClientDep
) -> UserWithPassword:
    try:
        user, temp = await auth.create_user(body.email, ctx, client)
    except UserExists:
        raise ApiError("a user with that email already exists", "conflict", 409) from None
    return UserWithPassword(
        user=UserOut.model_validate(user, from_attributes=True), temporary_password=temp
    )


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: uuid.UUID, auth: AuthDep, ctx: SessionDep, client: ClientDep
) -> UserOut:
    try:
        user = await auth.deactivate(user_id, ctx, client)
    except UserNotFound:
        raise _NOT_FOUND from None
    except CannotDeactivateSelf:
        raise ApiError("you cannot deactivate yourself", "conflict", 409) from None
    return UserOut.model_validate(user, from_attributes=True)


@router.post("/users/{user_id}/reactivate")
async def reactivate_user(
    user_id: uuid.UUID, auth: AuthDep, ctx: SessionDep, client: ClientDep
) -> UserOut:
    try:
        user = await auth.reactivate(user_id, ctx, client)
    except UserNotFound:
        raise _NOT_FOUND from None
    return UserOut.model_validate(user, from_attributes=True)


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: uuid.UUID, auth: AuthDep, ctx: SessionDep, client: ClientDep
) -> UserWithPassword:
    try:
        user, temp = await auth.reset_password(user_id, ctx, client)
    except UserNotFound:
        raise _NOT_FOUND from None
    return UserWithPassword(
        user=UserOut.model_validate(user, from_attributes=True), temporary_password=temp
    )


@router.get("/audit")
async def audit_log(
    audit: AuditDep,
    _ctx: SessionDep,
    user_id: uuid.UUID | None = None,
    action: AuditAction | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[AuditEntry]:
    rows, nxt = await audit.entries(user_id=user_id, action=action, cursor=cursor, limit=limit)
    return Page(
        items=[AuditEntry.model_validate(r, from_attributes=True) for r in rows], next_cursor=nxt
    )
