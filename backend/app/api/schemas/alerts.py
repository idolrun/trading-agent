import uuid
from datetime import datetime

from app.api.schemas.base import CamelModel


class AlertOut(CamelModel):
    id: uuid.UUID
    severity: str
    kind: str
    message: str
    acknowledged: bool
    card_id: uuid.UUID | None
    created_at: datetime
