from datetime import datetime
from typing import Any

from pydantic import Field

from app.api.schemas.base import CamelModel


class RegisterOptionsRequest(CamelModel):
    enrollment_token: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=64)


class RegisterVerifyRequest(CamelModel):
    enrollment_token: str = Field(min_length=1)
    credential: dict[str, Any]  # WebAuthn RegistrationResponseJSON (browser output, as is)


class RegisterVerifyResponse(CamelModel):
    credential_id: str  # base64url


class AssertionRequest(CamelModel):
    credential: dict[str, Any]  # WebAuthn AuthenticationResponseJSON


class SessionResponse(CamelModel):
    expires_at: datetime
    step_up_until: datetime | None = None


class StepUpResponse(CamelModel):
    step_up_until: datetime
