import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, StrictInt

from app.api.schemas.base import CamelModel


class EditsIn(CamelModel):
    qty: StrictInt | None = Field(default=None, ge=1)  # whole shares / contracts, typed by the user
    price: Decimal | None = None
    tp_split: list[int] | None = None
    breakeven_choice: Literal["user_avg", "provider_entry"] | None = None
    confirmed_underlying: str | None = None
    chosen_position_id: uuid.UUID | None = None
    expiry: date | None = None


class DecisionRequest(CamelModel):
    decision: Literal["approve", "reject", "edit"]
    version: int
    edits: EditsIn | None = None
