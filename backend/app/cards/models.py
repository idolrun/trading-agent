"""Cards, card history and approvals (database-schema.md §4.5-§4.7). State machine is F025."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlmodel import Field, SQLModel

from app.db import (
    bigint,
    check_in,
    choice,
    flag,
    identity_pk,
    jsonb,
    jsonb_object,
    now_default,
    positive_ids,
    timestamptz,
)


class CardSource(StrEnum):
    SIGNAL = "signal"
    SYSTEM = "system"
    MANUAL = "manual"


class CardAction(StrEnum):
    ENTRY = "entry"
    ADD = "add"
    TRIM = "trim"
    EXIT = "exit"
    STOP = "stop"
    TP = "tp"
    CLOSE = "close"
    REVIEW = "review"  # raw review / re-link review


class CardType(StrEnum):
    NORMAL = "normal"
    INFERRED = "inferred"
    REVIEW = "review"
    DISABLED = "disabled"


class CardState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    DISABLED = "disabled"


class CardReason(StrEnum):
    CREATED = "created"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    PRICE_BAND = "price_band"
    SUPERSEDED = "superseded"
    CORRECTION = "correction"
    DELETED = "deleted"
    RESTART = "restart"
    REVALIDATE_FAILED = "revalidate_failed"
    EDITED = "edited"
    REVIEW_COMPLETED = "review_completed"
    SUBMITTED = "submitted"


class CardActor(StrEnum):
    USER = "user"
    SYSTEM = "system"
    TIMER = "timer"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Card(SQLModel, table=True):
    """Current state. `card_id = uuid5(NAMESPACE, dedupe_key)`: re-processing cannot duplicate."""

    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("signal_id", "action"),
        ForeignKeyConstraint(
            ["provider_position_id", "provider_id"],
            ["provider_positions.provider_position_id", "provider_positions.provider_id"],
        ),
        check_in("source", CardSource),
        check_in("action", CardAction),
        check_in("card_type", CardType),
        check_in("state", CardState),
        CheckConstraint("(card_type = 'disabled') = (state = 'disabled')", name="disabled_type"),
        CheckConstraint(
            "state <> 'disabled' OR disabled_reason IS NOT NULL", name="disabled_has_reason"
        ),
        CheckConstraint("source <> 'signal' OR signal_id IS NOT NULL", name="signal_card_signal"),
        CheckConstraint("version >= 1", name="version_positive"),
        *positive_ids("provider_id"),
        Index(
            "ix_cards_pending",
            "state",
            sa_text("created_at DESC"),
            postgresql_where=sa_text("state = 'pending'"),
        ),
        Index("ix_cards_position_state", "provider_position_id", "state"),
        Index("ix_cards_lot_state", "lot_id", "state"),
    )

    card_id: uuid.UUID = Field(primary_key=True)
    dedupe_key: str = Field(unique=True)
    source: CardSource = choice()
    signal_id: uuid.UUID | None = Field(default=None, foreign_key="signals.signal_id")
    action: CardAction = choice()
    card_type: CardType = choice()
    state: CardState = choice()
    provider_id: int | None = bigint(nullable=True)
    provider_position_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = Field(default=None, foreign_key="lots.lot_id")
    proposal: dict[str, Any] = jsonb()  # Pydantic CardProposal
    posted_at: datetime = timestamptz()
    valid_until: datetime | None = timestamptz(nullable=True)
    disabled_reason: str | None = None
    note: str | None = None
    note_hidden: bool = flag()
    version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    created_at: datetime = now_default()
    updated_at: datetime = now_default()


class CardEvent(SQLModel, table=True):
    """Immutable audit: every card state transition."""

    __tablename__ = "card_events"
    __table_args__ = (
        check_in("from_state", CardState),
        check_in("to_state", CardState),
        check_in("reason_code", CardReason),
        check_in("actor", CardActor),
        Index("ix_card_events_card", "card_id", "id"),
    )

    id: int | None = identity_pk()
    card_id: uuid.UUID = Field(foreign_key="cards.card_id")
    from_state: CardState | None = choice(default=None, nullable=True)
    to_state: CardState = choice()
    reason_code: CardReason = choice()
    detail: dict[str, Any] = jsonb_object()
    actor: CardActor = choice()
    proposal_snapshot: dict[str, Any] | None = jsonb(nullable=True)
    at: datetime = now_default()


class Approval(SQLModel, table=True):
    """Immutable audit: the one decision on a card."""

    __tablename__ = "approvals"
    __table_args__ = (
        check_in("decision", Decision),
        CheckConstraint(
            "(decision = 'approve') = (approved_proposal IS NOT NULL)",
            name="approved_proposal_iff_approve",
        ),
    )

    approval_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    card_id: uuid.UUID = Field(foreign_key="cards.card_id", unique=True)
    decision: Decision = choice()
    edits: dict[str, Any] = jsonb_object()
    approved_proposal: dict[str, Any] | None = jsonb(nullable=True)
    card_version: int
    session_id: uuid.UUID = Field(foreign_key="auth_sessions.session_id")
    step_up: bool = flag()
    decided_at: datetime = now_default()
