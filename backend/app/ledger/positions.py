"""Provider position ledger rules (FR-16, C72, TRD §6): pure transitions, no SQL.

Every provider's called position is tracked whether or not the user traded (stale entries
too, FR-1c). An illegal transition raises `InvalidTransition` -> review card; nothing is
silently ignored.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.instruments import Instrument, OptionContract, Stock
from app.interpret.models import MsgType
from app.ledger.models import PositionChange, PositionStatus, ProviderPosition
from app.validate.normalize import NormalizedSignal

FRACTIONS = {"half": Decimal("0.5"), "third": Decimal(1) / 3, "quarter": Decimal("0.25")}
_FOUR_PLACES = Decimal("0.0001")  # remaining_fraction numeric(5,4)
OPENING = {MsgType.ENTRY, MsgType.STOCK_ENTRY}
NO_LEDGER_CHANGE = {MsgType.SOFT_EXIT, MsgType.TP_HIT_NOTICE}  # C52; OD-18 proposal


class InvalidTransition(ValueError):
    pass


class PositionState(BaseModel):
    """The provider's view of one position (mirrors `provider_positions` value columns)."""

    model_config = ConfigDict(frozen=True)

    instrument: Literal["option", "stock"]
    underlying: str
    expiry: date | None = None
    strike: Decimal | None = None
    option_right: Literal["call", "put"] | None = None
    provider_entry_price: Decimal | None = None
    remaining_fraction: Decimal = Decimal(1)
    stop_level: Decimal | None = None
    stop_basis: Literal["premium", "underlying"] | None = None
    tp_levels: tuple[Decimal, ...] = ()
    status: PositionStatus = PositionStatus.OPEN
    is_corrected: bool = False


@dataclass(frozen=True)
class PositionUpdate:
    change: PositionChange
    before: PositionState | None
    after: PositionState
    add_price: Decimal | None = None  # adds are recorded in the event, not averaged


def state_of(row: ProviderPosition) -> PositionState:
    return PositionState(
        instrument=row.instrument,
        underlying=row.underlying,
        expiry=row.expiry,
        strike=row.strike,
        option_right=row.option_right,
        provider_entry_price=row.provider_entry_price,
        remaining_fraction=row.remaining_fraction,
        stop_level=row.stop_level,
        stop_basis=row.stop_basis,
        tp_levels=tuple(row.tp_levels),
        status=row.status,
        is_corrected=row.is_corrected,
    )


def apply_to_position(
    position: PositionState | None, signal: NormalizedSignal
) -> PositionUpdate | None:
    """The ledger effect of one (already linked) signal; None = no ledger change."""
    t = signal.msg_type
    if t in NO_LEDGER_CHANGE:
        return None
    if t in OPENING or t == MsgType.ADD:
        if position is None:
            if t == MsgType.ADD:
                raise InvalidTransition("add without a linked provider position")
            return PositionUpdate(PositionChange.OPEN, None, _opened(signal))
        _require_live(position, t)
        if not _same_contract(position, signal):
            raise InvalidTransition("entry names a different contract than the linked position")
        price = signal.entry_price.value if signal.entry_price else None
        return PositionUpdate(PositionChange.ADD, position, position, add_price=price)
    if t not in {
        MsgType.TRIM,
        MsgType.FULL_EXIT,
        MsgType.STOCK_EXIT,
        MsgType.STOP_SET,
        MsgType.STOP_MOVE,
        MsgType.STOP_CANCEL,
        MsgType.TP_SET,
    }:
        return None  # commentary, watch, chatter, corrections (F038) ...
    if position is None:
        raise InvalidTransition(f"{t} without a linked provider position")
    _require_live(position, t)

    if t in (MsgType.FULL_EXIT, MsgType.STOCK_EXIT) or (
        t == MsgType.TRIM and signal.exit_fraction == "all"
    ):
        return PositionUpdate(
            PositionChange.EXIT,
            position,
            position.model_copy(update={"status": PositionStatus.CLOSED}),
        )
    if t == MsgType.TRIM:
        remaining = position.remaining_fraction
        if signal.exit_fraction in FRACTIONS:
            remaining = (remaining * (1 - FRACTIONS[signal.exit_fraction])).quantize(
                _FOUR_PLACES, ROUND_HALF_UP
            )
        # fraction not stated: provider trimmed an unknown amount; keep the last known value
        after = position.model_copy(
            update={"status": PositionStatus.TRIMMED, "remaining_fraction": remaining}
        )
        return PositionUpdate(PositionChange.TRIM, position, after)
    if t == MsgType.STOP_CANCEL or signal.stop_removed:
        after = position.model_copy(update={"stop_level": None, "stop_basis": None})
        return PositionUpdate(PositionChange.STOP_CANCEL, position, after)
    if t in (MsgType.STOP_SET, MsgType.STOP_MOVE):
        level = signal.stop_level.value if signal.stop_level else None
        if signal.breakeven_stop and level is None:
            level = position.provider_entry_price  # "BE stop"
        if level is None:
            raise InvalidTransition("stop without a level")
        after = position.model_copy(
            update={"stop_level": level, "stop_basis": signal.stop_basis or "premium"}
        )
        return PositionUpdate(PositionChange(t.value), position, after)
    # tp_set
    if not signal.tp_levels:
        raise InvalidTransition("tp_set without levels")
    after = position.model_copy(update={"tp_levels": tuple(p.value for p in signal.tp_levels)})
    return PositionUpdate(PositionChange.TP_SET, position, after)


def apply_correction(position: PositionState | None, changes: dict[str, Any]) -> PositionUpdate:
    """Manual correction (C73, F047): any field; always marked corrected."""
    values = (position.model_dump() if position else {}) | changes | {"is_corrected": True}
    after = PositionState.model_validate(values)
    if position is None:
        return PositionUpdate(PositionChange.OPEN, None, after)
    return PositionUpdate(PositionChange.CORRECT, position, after)


def _opened(signal: NormalizedSignal) -> PositionState:
    instrument = "stock" if signal.msg_type == MsgType.STOCK_ENTRY else signal.instrument
    if instrument not in ("option", "stock") or signal.underlying is None:
        raise InvalidTransition("entry without a complete instrument")
    if instrument == "option" and None in (signal.expiry, signal.strike, signal.right):
        raise InvalidTransition("option entry without expiry/strike/right")
    first_price = signal.entry_price or signal.entry_price_low
    return PositionState(
        instrument=instrument,
        underlying=signal.underlying,
        expiry=signal.expiry if instrument == "option" else None,
        strike=signal.strike if instrument == "option" else None,
        option_right=signal.right if instrument == "option" else None,
        provider_entry_price=first_price.value if first_price else None,  # C29 breakeven
        stop_level=signal.stop_level.value if signal.stop_level else None,
        stop_basis=(signal.stop_basis or "premium") if signal.stop_level else None,
        tp_levels=tuple(p.value for p in signal.tp_levels),
    )


def _same_contract(position: PositionState, signal: NormalizedSignal) -> bool:
    """Fields the signal states must match; unstated fields do not contradict."""
    stated = {
        "underlying": signal.underlying,
        "expiry": signal.expiry,
        "strike": signal.strike,
        "option_right": signal.right,
    }
    return all(v is None or getattr(position, k) == v for k, v in stated.items())


def _require_live(position: PositionState, t: MsgType) -> None:
    if position.status == PositionStatus.CLOSED:
        raise InvalidTransition(f"{t} on a closed provider position")


def contract_of(position: PositionState) -> Instrument:
    """The tradable instrument the provider position refers to."""
    if position.instrument == "stock":
        return Stock(symbol=position.underlying)
    assert position.expiry and position.strike and position.option_right  # noqa: S101 — shape
    return OptionContract(
        underlying=position.underlying,
        expiry=position.expiry,
        strike=position.strike,
        right=position.option_right,
    )
