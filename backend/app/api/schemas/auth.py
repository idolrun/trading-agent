from datetime import datetime

from pydantic import Field

from app.api.schemas.base import CamelModel


class LoginRequest(CamelModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(CamelModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class SessionResponse(CamelModel):
    email: str
    expires_at: datetime
    must_change_password: bool = False
