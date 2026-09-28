import uuid

from pydantic import Field

from app.api.schemas.base import CamelModel


class VapidKeyResponse(CamelModel):
    public_key: str


class PushKeys(CamelModel):
    p256dh: str = Field(min_length=1, max_length=512)
    auth: str = Field(min_length=1, max_length=512)


class SubscribeRequest(CamelModel):
    endpoint: str = Field(pattern=r"^https://", max_length=2048)
    keys: PushKeys
    label: str | None = Field(default=None, max_length=100)


class SubscribeResponse(CamelModel):
    subscription_id: uuid.UUID
