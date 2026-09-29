"""Read side of cards (FR-25, 7.1 step 4): the proposal plus where every value came from."""

import uuid
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.pagination import cut, keyset
from app.api.schemas.cards import (
    ApprovalOut,
    BreakevenOut,
    CardDetail,
    CardEventOut,
    CardOut,
    ContractOut,
    InferredField,
    ProposalOut,
    SourceMessage,
    SpanOut,
    StopOut,
    TpLegOut,
)
from app.cards.models import Approval, Card, CardEvent
from app.inbox.models import EventType, MessageEvent
from app.interpret.models import Signal
from app.ledger.models import ProviderPosition


async def list_cards(
    db: AsyncSession, *, state: str | None, provider_id: int | None, cursor: str | None, limit: int
) -> tuple[list[CardOut], str | None]:
    query = select(Card)
    if state is not None:
        query = query.where(col(Card.state) == state)
    if provider_id is not None:
        query = query.where(col(Card.provider_id) == provider_id)
    rows = (
        await db.scalars(
            keyset(query, col(Card.created_at), col(Card.card_id), cursor, limit, uuid.UUID)
        )
    ).all()
    page, nxt = cut(rows, limit, lambda c: (c.created_at, c.card_id))
    return await _outs(db, page), nxt


async def get_card(db: AsyncSession, card_id: uuid.UUID) -> CardDetail | None:
    card = await db.get(Card, card_id)
    if card is None:
        return None
    [out] = await _outs(db, [card])
    events = await db.scalars(
        select(CardEvent).where(col(CardEvent.card_id) == card_id).order_by(col(CardEvent.id))
    )
    approval = await db.scalar(select(Approval).where(col(Approval.card_id) == card_id))
    return CardDetail(
        **out.model_dump(),
        events=[
            CardEventOut(
                from_state=e.from_state,
                to_state=e.to_state,
                reason_code=e.reason_code,
                actor=e.actor,
                detail=e.detail,
                at=e.at,
            )
            for e in events
        ],
        approval=ApprovalOut(
            decision=approval.decision,
            edits=approval.edits,
            card_version=approval.card_version,
            step_up=approval.step_up,
            decided_at=approval.decided_at,
        )
        if approval
        else None,
    )


async def _outs(db: AsyncSession, cards: list[Card]) -> list[CardOut]:
    signal_ids = [c.signal_id for c in cards if c.signal_id]
    signals = {
        s.signal_id: s
        for s in (await db.scalars(select(Signal).where(col(Signal.signal_id).in_(signal_ids))))
    }
    position_ids = [c.provider_position_id for c in cards if c.provider_position_id]
    positions = {
        p.provider_position_id: p
        for p in await db.scalars(
            select(ProviderPosition).where(
                col(ProviderPosition.provider_position_id).in_(position_ids)
            )
        )
    }
    inferred = {sid: _inferred(s.fields) for sid, s in signals.items()}
    keys: set[tuple[int, str, int]] = {
        (s.message_id, s.event_type, s.event_seq) for s in signals.values()
    }
    context_ids = {f.source_message_id for fs in inferred.values() for f in fs}
    messages = await _messages(db, keys, context_ids)

    out = []
    for card in cards:
        sig = signals.get(card.signal_id) if card.signal_id else None
        pos = positions.get(card.provider_position_id) if card.provider_position_id else None
        fields = sig.fields if sig else {}
        sources: list[SourceMessage] = []
        if sig is not None:
            own = messages.get((sig.message_id, sig.event_type, sig.event_seq))
            refs = {f.source_message_id for f in inferred[sig.signal_id]}
            if own is not None:
                own = own.model_copy(update={"spans": _spans(sig.fields)})
            sources = [
                m for m in [own, *(messages.get((r, EventType.CREATE, 0)) for r in refs)] if m
            ]
        out.append(
            CardOut(
                id=card.card_id,
                message_id=sig.message_id if sig else None,
                signal_index=sig.idx if sig else None,
                action=card.action,
                card_type=card.card_type,
                state=card.state,
                provider_id=card.provider_id,
                provider_position_id=card.provider_position_id,
                lot_id=card.lot_id,
                underlying=pos.underlying if pos else _value(fields, "underlying"),
                contract=ContractOut(
                    instrument=pos.instrument,
                    underlying=pos.underlying,
                    expiry=pos.expiry,
                    strike=pos.strike,
                    right=pos.option_right,
                )
                if pos
                else None,
                proposal=_proposal(card.proposal),
                posted_at=card.posted_at,
                valid_until=card.valid_until,
                note=None if card.note_hidden else card.note,
                note_hidden=card.note_hidden,
                version=card.version,
                source_messages=sources,
                inferred_fields=inferred[sig.signal_id] if sig else [],
            )
        )
    return out


async def _messages(
    db: AsyncSession, keys: set[tuple[int, str, int]], context_ids: set[int]
) -> dict[tuple[int, str, int], SourceMessage]:
    wanted = keys | {(m, EventType.CREATE.value, 0) for m in context_ids}
    if not wanted:
        return {}
    rows = await db.scalars(
        select(MessageEvent).where(
            tuple_(
                col(MessageEvent.message_id),
                col(MessageEvent.event_type),
                col(MessageEvent.event_seq),
            ).in_(list(wanted))
        )
    )
    return {
        (m.message_id, m.event_type, m.event_seq): SourceMessage(
            message_id=m.message_id,
            author_id=m.provider_id,
            posted_at=m.posted_at,
            text=m.text,
            spans=[],
        )
        for m in rows
    }


def _inferred(fields: dict[str, Any]) -> list[InferredField]:
    """Fields taken from an earlier message (`context` provenance), highlighted on the card."""
    found = []
    for name, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            source = item.get("source") if isinstance(item, dict) else None
            if isinstance(source, dict) and source.get("kind") == "context":
                found.append(
                    InferredField(field=name, source_message_id=source["source_message_id"])
                )
    return found


def _spans(fields: dict[str, Any]) -> list[SpanOut]:
    """Literal provenance with offsets (`verify_literal_spans` fills them before storage)."""
    found = []
    for name, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            source = item.get("source") if isinstance(item, dict) else None
            if isinstance(source, dict) and source.get("kind") == "literal":
                start, end = source.get("start"), source.get("end")
                if isinstance(start, int) and isinstance(end, int):
                    found.append(SpanOut(field=name, start=start, end=end))
    return found


def _value(fields: dict[str, Any], name: str) -> str | None:
    item = fields.get(name)
    return str(item["value"]) if isinstance(item, dict) and "value" in item else None


def _proposal(p: dict[str, Any]) -> ProposalOut:
    stop = p.get("stop")
    breakeven = p.get("breakeven_options")
    return ProposalOut(
        qty=p.get("qty"),
        price=p.get("limit_price"),
        signal_price=p.get("signal_price"),
        stop=StopOut.model_validate(stop) if stop else None,
        tp_legs=[TpLegOut.model_validate(leg) for leg in p.get("tp_legs", [])],
        warnings=p.get("warnings", []),
        labels=p.get("labels", []),
        review_reasons=p.get("review_reasons", []),
        missing_fields=p.get("missing_fields", []),
        suggested_underlying=p.get("suggested_underlying"),
        candidate_positions=p.get("candidate_position_ids", []),
        breakeven_options=BreakevenOut.model_validate(breakeven) if breakeven else None,
        disabled_reason=p.get("disabled_reason"),
    )
