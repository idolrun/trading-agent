"""Decision engine inputs and outputs (TRD §6). No logic here."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.cards.models import CardAction, CardType
from app.interpret.signal import ParsedSignal
from app.ledger.correlation import LinkDecision
from app.ledger.positions import PositionState
from app.runtime_config.schema import RuntimeConfig
from app.validate.service import ValidationResult


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StopProposal(_Frozen):
    cancel: bool = False
    level: Decimal | None = None  # None with breakeven options = the user picks (C29)
    basis: Literal["premium", "underlying"] | None = None
    held_by: Literal["broker", "system"] | None = None  # underlying stops are system-held (C49)
    candle_close: bool = False  # shown as information only (C49)


class TpLeg(_Frozen):
    level: Decimal
    qty: int


class BreakevenOptions(_Frozen):
    user_avg: Decimal | None
    provider_entry: Decimal | None


class ProposedAction(_Frozen):
    """One card. Stored as the card `proposal` (F026)."""

    action: CardAction
    card_type: CardType
    qty: int | None = None  # None = the user enters it (C26, C52, C82)
    limit_price: Decimal | None = None  # entries/adds only; exits start at the mid (C25)
    signal_price: Decimal | None = None  # entries/adds: stated price or top of range (C22 band)
    stop: StopProposal | None = None
    tp_legs: list[TpLeg] = []
    labels: list[str] = []
    warnings: list[str] = []
    review_reasons: list[str] = []
    missing_fields: list[str] = []
    candidate_position_ids: list[uuid.UUID] = []
    disabled_reason: str | None = None
    valid_until: datetime | None = None
    breakeven_options: BreakevenOptions | None = None
    suggested_underlying: str | None = None


@dataclass(frozen=True)
class LotView:
    lot_id: uuid.UUID
    qty: int
    avg_price: Decimal | None


@dataclass(frozen=True)
class FlagsView:
    kill_switch: bool = False
    entries_blocked: bool = False


@dataclass(frozen=True)
class OpenLotBrief:
    underlying: str
    option_right: str | None  # "call" / "put"; None = stock


@dataclass(frozen=True)
class RiskSnapshot:
    """Portfolio-wide, foreign lots included (C47). None = unknown: never assumed 0."""

    realized_today: Decimal | None
    unrealized: Decimal | None
    open_positions: int
    exposure: Decimal | None  # cost basis of open lots
    lots: tuple[OpenLotBrief, ...] = ()


@dataclass(frozen=True)
class DecisionInputs:
    signal: ParsedSignal
    validation: ValidationResult
    link: LinkDecision | None
    position: PositionState | None  # the linked provider position, before this signal
    lot: LotView | None  # the user's open lot on that position
    config: RuntimeConfig
    tick: Decimal | None  # MarketData.tick_size at the signal price (OD-25); entries only
    posted_at: datetime
    stale: bool = False  # FR-1c: processed after stale_entry_s
    late: bool = False
    flags: FlagsView = FlagsView()
    risk: RiskSnapshot | None = None  # None = could not be built -> entries disabled
