"""User edits to a pending card's proposal (F036). Pure, no SQL.

An edit fills what the card asks for (`missing_fields`) or adjusts qty / limit / TP split /
breakeven choice. Numbers the user types are the user's, never the system's (C78).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.decide import rules
from app.decide.types import ProposedAction

APPROVE_EDITS = frozenset({"qty", "tp_split", "breakeven_choice"})  # the rest need `edit` first


class Edits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    qty: StrictInt | None = Field(default=None, ge=1)  # whole shares / contracts, user-typed
    price: Decimal | None = Field(default=None, gt=0)
    tp_split: list[int] | None = None
    breakeven_choice: Literal["user_avg", "provider_entry"] | None = None
    confirmed_underlying: str | None = Field(default=None, pattern=r"^[A-Z][A-Z.]{0,9}$")
    chosen_position_id: uuid.UUID | None = None
    expiry: date | None = None

    def given(self) -> set[str]:
        return set(self.model_dump(exclude_none=True))


class EditInvalid(ValueError):
    pass


# missing field (decide) -> the edit that supplies it
SUPPLIES = {
    "chosen_position_id": "chosen_position_id",
    "underlying_confirmation": "confirmed_underlying",
    "entry_price": "price",
    "stop_level": "breakeven_choice",
    "expiry": "expiry",
}


def apply_edits(proposal: ProposedAction, edits: Edits) -> ProposedAction:
    """The proposal after the edits; raises EditInvalid for an edit that cannot apply."""
    update: dict[str, object] = {}
    if edits.qty is not None:
        update["qty"] = edits.qty
        if edits.tp_split is None and proposal.tp_levels:  # C105: legs follow the user's qty
            update["tp_legs"] = rules.tp_legs(proposal.tp_levels, edits.qty)
    if edits.price is not None:
        update["limit_price"] = edits.price
        if proposal.signal_price is None:
            update["signal_price"] = edits.price
    if edits.tp_split is not None:
        legs = proposal.tp_legs
        if proposal.tp_levels:  # entry/add: legs exist only for the qty the user entered
            legs = rules.tp_legs(proposal.tp_levels, edits.qty or proposal.qty)
        if len(edits.tp_split) != len(legs) or any(q < 1 for q in edits.tp_split):
            raise EditInvalid("tpSplit needs one qty >= 1 per take-profit leg")
        qty = edits.qty or proposal.qty
        if qty is not None and sum(edits.tp_split) > qty:
            raise EditInvalid("tpSplit exceeds the card qty")
        update["tp_legs"] = [
            leg.model_copy(update={"qty": q}) for leg, q in zip(legs, edits.tp_split, strict=True)
        ]
    if edits.breakeven_choice is not None:
        options, stop = proposal.breakeven_options, proposal.stop
        level = getattr(options, edits.breakeven_choice) if options else None
        if stop is None or level is None:
            raise EditInvalid("this card has no such breakeven price")
        update["stop"] = stop.model_copy(update={"level": level})
    if edits.confirmed_underlying is not None and (
        edits.confirmed_underlying != proposal.suggested_underlying
    ):
        raise EditInvalid("confirmedUnderlying must match the suggested underlying")
    if (
        edits.chosen_position_id is not None
        and proposal.candidate_position_ids
        and edits.chosen_position_id not in proposal.candidate_position_ids
    ):
        raise EditInvalid("chosenPositionId is not one of the candidate positions")
    given = edits.given()
    update["missing_fields"] = [f for f in proposal.missing_fields if SUPPLIES.get(f) not in given]
    return proposal.model_copy(update=update)
