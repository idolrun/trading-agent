import uuid
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.api.schemas.base import CamelModel, DiscordId, Num
from app.instruments import Instrument, OptionContract, Stock
from app.ledger.models import PositionStatus

Key = Annotated[str, Field(min_length=1, max_length=100)]  # one submission per key
Reason = Annotated[str, Field(min_length=1)]


class ManualResultOut(CamelModel):
    card_id: uuid.UUID
    state: str
    order_ids: list[uuid.UUID]


class ExitRequest(CamelModel):
    lot_id: uuid.UUID
    qty: int = Field(ge=1)
    idempotency_key: Key


class StopRequest(CamelModel):
    lot_id: uuid.UUID
    op: Literal["add", "move", "cancel"]
    level: Num | None = Field(default=None, gt=0)
    basis: Literal["premium", "underlying"] | None = None
    idempotency_key: Key


class ContractIn(CamelModel):
    instrument: Literal["option", "stock"]
    underlying: str  # option root or stock ticker
    expiry: date | None = None
    strike: Num | None = None
    right: Literal["call", "put"] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        option = (self.expiry, self.strike, self.right)
        if self.instrument == "option" and None in option:
            raise ValueError("an option needs expiry, strike and right")
        if self.instrument == "stock" and any(v is not None for v in option):
            raise ValueError("a stock has no expiry, strike or right")
        return self

    def to_instrument(self) -> Instrument:
        if self.instrument == "stock":
            return Stock(symbol=self.underlying)
        assert self.expiry and self.strike and self.right  # noqa: S101 — checked above
        return OptionContract(
            underlying=self.underlying, expiry=self.expiry, strike=self.strike, right=self.right
        )


class EntryRequest(CamelModel):
    instrument: ContractIn
    qty: int = Field(ge=1)
    limit_price: Num = Field(gt=0)
    idempotency_key: Key


class KillSwitchRequest(CamelModel):
    active: bool
    reason: Reason


class KillSwitchOut(CamelModel):
    active: bool


class FlattenRequest(CamelModel):
    scope: Literal["options", "stocks", "all"]
    idempotency_key: Key


class FlattenedLot(ManualResultOut):
    lot_id: uuid.UUID | None
    blocked: str | None  # why the shared checks refused this lot's exit


class FlattenOut(CamelModel):
    kill_switch: bool
    lots: list[FlattenedLot]


class PositionFields(CamelModel):
    underlying: str | None = None
    expiry: date | None = None
    strike: Num | None = Field(default=None, gt=0)
    option_right: Literal["call", "put"] | None = Field(default=None, alias="right")
    provider_entry_price: Num | None = Field(default=None, gt=0, alias="entryPrice")


class AddPositionRequest(PositionFields):
    provider_id: DiscordId = Field(gt=0)
    instrument: Literal["option", "stock"]
    underlying: str
    reason: Reason


class EditPositionRequest(PositionFields):
    """Only the fields sent change; an explicit null clears one (e.g. `stopLevel`)."""

    stop_level: Num | None = Field(default=None, gt=0)
    stop_basis: Literal["premium", "underlying"] | None = None
    tp_levels: list[Num] | None = None
    status: PositionStatus | None = None
    reason: Reason


class RelinkRequest(CamelModel):
    signal_id: uuid.UUID
    provider_position_id: uuid.UUID
    reason: Reason


class RelinkOut(CamelModel):
    card_ids: list[uuid.UUID]


class AdjustRequest(CamelModel):
    qty: int = Field(ge=0)
    reason: Reason


class ProfileRequest(CamelModel):
    aliases: list[str] | None = None
    lexicon: list[str] | None = None
    habits: list[str] | None = None
    reason: Reason


class ProfileOut(CamelModel):
    aliases: list[str] = Field(default_factory=list)
    lexicon: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
