import uuid
from datetime import datetime
from typing import Any

from pydantic import EmailStr, field_validator

from app.api.schemas.base import CamelModel
from app.auth.models import AuditAction


class UserOut(CamelModel):
    user_id: uuid.UUID
    email: str
    is_active: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


class CreateUserRequest(CamelModel):
    email: EmailStr


class UserWithPassword(CamelModel):
    """The temporary password is returned once and never stored (only its hash)."""

    user: UserOut
    temporary_password: str


class AuditEntry(CamelModel):
    id: int
    at: datetime
    user_id: uuid.UUID | None
    user_email: str | None
    action: AuditAction
    resource_type: str | None
    resource_id: str | None
    ip: str | None
    request_id: str | None
    details: dict[str, Any]

    @field_validator("ip", mode="before")
    @classmethod
    def _ip_text(cls, value: object) -> str | None:
        return None if value is None else str(value)  # psycopg returns an ipaddress object
