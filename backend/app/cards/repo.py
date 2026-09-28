"""Card persistence: insert once, lock, and every transition with its `card_events` row."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.cards.models import Card, CardAction, CardActor, CardEvent, CardReason, CardState
from app.cards.state import check_transition

# card_id = uuid5(CARD_NAMESPACE, dedupe_key): re-processing an event yields the same id.
CARD_NAMESPACE = uuid.UUID("6b1f0c1e-5d3a-4c1e-9a3e-0c2d7b8f4a51")


def card_id_for(dedupe_key: str) -> uuid.UUID:
    return uuid.uuid5(CARD_NAMESPACE, dedupe_key)


async def insert(db: AsyncSession, card: Card, *, at: datetime | None = None) -> bool:
    """Insert with its `created` event; False = already exists (idempotent re-run, no event)."""
    check_transition(None, card.state, CardReason.CREATED)
    card.card_id = card_id_for(card.dedupe_key)
    now = at or datetime.now(UTC)
    values = card.model_dump() | {"created_at": now, "updated_at": now}
    inserted = await db.scalar(
        pg_insert(Card).values(**values).on_conflict_do_nothing().returning(col(Card.card_id))
    )
    if inserted is None:
        return False
    db.add(
        CardEvent(
            card_id=card.card_id,
            from_state=None,
            to_state=card.state,
            reason_code=CardReason.CREATED,
            actor=CardActor.SYSTEM,
            proposal_snapshot=card.proposal,
            at=now,
        )
    )
    await db.flush()
    return True


async def lock(db: AsyncSession, card_id: uuid.UUID) -> Card | None:
    """`FOR UPDATE`: decision, timer and supersession serialize here; the loser then gets
    IllegalTransition from `transition`."""
    card: Card | None = await db.scalar(
        select(Card).where(col(Card.card_id) == card_id).with_for_update()
    )
    return card


async def transition(
    db: AsyncSession,
    card: Card,
    to_state: CardState,
    reason: CardReason,
    actor: CardActor,
    *,
    detail: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> Card:
    """Apply one legal transition to a locked card. A new `proposal` (edit / review
    completion) bumps `version` so an approval of a stale version can be refused (F036)."""
    check_transition(card.state, to_state, reason)
    now = at or datetime.now(UTC)
    from_state = card.state
    card.state = to_state
    card.updated_at = now
    if proposal is not None:
        card.proposal = proposal
        card.version += 1
    db.add(
        CardEvent(
            card_id=card.card_id,
            from_state=from_state,
            to_state=to_state,
            reason_code=reason,
            actor=actor,
            detail=detail or {},
            proposal_snapshot=proposal,
            at=now,
        )
    )
    await db.flush()
    return card


async def pending_on_position(
    db: AsyncSession, provider_position_id: uuid.UUID, actions: Iterable[CardAction]
) -> list[Card]:
    """Locked pending cards of these actions on a position (supersession, OD-14)."""
    rows = await db.scalars(
        select(Card)
        .where(
            col(Card.provider_position_id) == provider_position_id,
            col(Card.state) == CardState.PENDING,
            col(Card.action).in_(list(actions)),
        )
        .order_by(col(Card.created_at), col(Card.card_id))
        .with_for_update()
    )
    return list(rows.all())
