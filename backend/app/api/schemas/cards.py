import uuid
from datetime import date, datetime
from typing import Any

from app.api.schemas.base import CamelModel, DiscordId, Num


class ContractOut(CamelModel):
    instrument: str
    underlying: str
    expiry: date | None
    strike: Num | None
    right: str | None


class StopOut(CamelModel):
    cancel: bool
    level: Num | None
    basis: str | None
    held_by: str | None
    candle_close: bool


class TpLegOut(CamelModel):
    level: Num
    qty: int


class BreakevenOut(CamelModel):
    user_avg: Num | None
    provider_entry: Num | None


class ProposalOut(CamelModel):
    qty: int | None
    price: Num | None  # buy limit (entries/adds); exits start at the mid
    signal_price: Num | None
    stop: StopOut | None
    tp_levels: list[Num]  # stated take-profits; legs are split from the qty the user enters
    tp_legs: list[TpLegOut]
    warnings: list[str]
    labels: list[str]
    review_reasons: list[str]
    missing_fields: list[str]
    suggested_underlying: str | None
    candidate_positions: list[uuid.UUID]
    breakeven_options: BreakevenOut | None
    disabled_reason: str | None


class SpanOut(CamelModel):
    """A literal span of a message: `text[start:end]` in Unicode code points (FR-7, C31)."""

    field: str
    start: int
    end: int


class SourceMessage(CamelModel):
    message_id: DiscordId
    author_id: DiscordId
    posted_at: datetime
    text: str | None
    spans: list[SpanOut]  # the card's signal's literal fields in this message; else empty


class InferredField(CamelModel):
    field: str
    source_message_id: DiscordId


class CardOut(CamelModel):
    id: uuid.UUID
    message_id: DiscordId | None
    signal_index: int | None
    signal_id: uuid.UUID | None
    action: str
    card_type: str
    state: str
    provider_id: DiscordId | None
    provider_name: str | None  # latest Discord display name seen for the provider; None until known
    provider_position_id: uuid.UUID | None
    lot_id: uuid.UUID | None
    underlying: str | None
    contract: ContractOut | None
    proposal: ProposalOut
    posted_at: datetime
    valid_until: datetime | None
    note: str | None
    note_hidden: bool
    version: int
    source_messages: list[SourceMessage]
    inferred_fields: list[InferredField]


class CardEventOut(CamelModel):
    from_state: str | None
    to_state: str
    reason_code: str
    actor: str
    detail: dict[str, Any]
    at: datetime


class ApprovalOut(CamelModel):
    decision: str
    edits: dict[str, Any]
    card_version: int
    step_up: bool
    decided_at: datetime


class CardDetail(CardOut):
    events: list[CardEventOut]
    approval: ApprovalOut | None


class CheckOut(CamelModel):
    name: str  # hours | validity | quote | quote_fresh | price_band | superseded | risk | ...
    status: str  # pass | review | blocked
    reason: str | None
    detail: str | None


class PreflightOut(CamelModel):
    """The revalidation checks run now, read-only (nothing is decided or stored). `applicable`
    false = nothing to check (a review card, or a card that is no longer waiting)."""

    applicable: bool
    verdict: str  # pass | review | blocked
    checks: list[CheckOut]
    qty: int | None
    bid: Num | None
    ask: Num | None
    last: Num | None
    quote_at: datetime | None
    signal_price: Num | None
    limit_price: Num | None
    est_cost: Num | None
    equity: Num | None
    cash: Num | None
    buying_power: Num | None
    projected_buying_power: Num | None
    exposure: Num | None
    working_exposure: Num | None
    projected_exposure: Num | None
    max_exposure: Num | None
    open_positions: int | None
    max_open_positions: int | None
    daily_pnl: Num | None
    max_daily_loss: Num | None
