"""What an order should be, and who allowed it. No I/O."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.execution.models import AutoReason, OrderKind, OrderType, Side, TimeInForce
from app.instruments import Instrument

NAMESPACE = uuid.UUID("3d8f1b2a-7c4e-4a9b-b6d1-5e2f8a0c9d17")
CLIENT_ID_PREFIX = "cta-"  # broker orders with this prefix are ours (F046 recovery)


class OrderIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    card_id: uuid.UUID | None = None
    action_seq: int = 0  # n-th order action of the card (entry=0, its bracket legs 1.., ...)
    parent_order_id: uuid.UUID | None = None
    kind: OrderKind
    side: Side
    instrument: Instrument
    qty: int = Field(gt=0)
    order_type: OrderType
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: TimeInForce
    provider_position_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    oco_group_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Approved:
    """A user approval of a card (AC-2)."""

    approval_id: uuid.UUID


@dataclass(frozen=True)
class Auto:
    """One of the pre-approved automatic actions (PRD §9)."""

    reason: AutoReason
    seq: int = 0
    ref: str | None = None  # what triggered it (a fired stop, a cutoff date): one order per ref


def client_order_id(intent: OrderIntent, auth: Approved | Auto) -> str:
    """Deterministic (TRD §5.6): a re-executed graph node derives the same id -> no duplicate."""
    if isinstance(auth, Approved):
        if intent.card_id is None:
            raise ValueError("an approved order needs its card")
        key = f"{intent.card_id}:{intent.action_seq}"
        if intent.parent_order_id is not None:  # a replacement (bracket resize, stop move)
            key += f":{intent.parent_order_id}"
    else:
        anchor = intent.parent_order_id or intent.lot_id or intent.card_id
        if anchor is None:
            raise ValueError("an automatic order needs a parent order, lot or card")
        key = f"{anchor}:{auth.reason}:{auth.seq}"
        if auth.ref is not None:
            key += f":{auth.ref}"
    return CLIENT_ID_PREFIX + str(uuid.uuid5(NAMESPACE, key))
