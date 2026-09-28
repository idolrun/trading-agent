"""The live message feed (FR-10, FR-1c): every event, with labels; nothing is hidden."""

from collections import defaultdict
from typing import Any

from sqlalchemy import ColumnElement, and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.pagination import cut, keyset
from app.api.schemas.feed import FeedItem
from app.cards.models import Card
from app.inbox.models import InboxItem, InboxStatus, MessageEvent, SkipReason
from app.interpret.models import Signal

LABELS: dict[str, ColumnElement[bool]] = {
    "stale": col(InboxItem.status) == InboxStatus.STALE,
    "late": col(InboxItem.late).is_(True),
    "backfilled": col(MessageEvent.backfilled).is_(True),
    "muted": col(InboxItem.skip_reason) == SkipReason.MUTED_AUTHOR,
    "channel_not_configured": col(InboxItem.skip_reason) == SkipReason.CHANNEL_NOT_CONFIGURED,
    "failed": col(InboxItem.status) == InboxStatus.FAILED,
}


async def list_feed(
    db: AsyncSession, *, provider_id: int | None, label: str | None, cursor: str | None, limit: int
) -> tuple[list[FeedItem], str | None]:
    query: Any = select(MessageEvent, InboxItem).outerjoin(
        InboxItem,
        and_(
            col(InboxItem.message_id) == col(MessageEvent.message_id),
            col(InboxItem.event_type) == col(MessageEvent.event_type),
            col(InboxItem.event_seq) == col(MessageEvent.event_seq),
        ),
    )
    if provider_id is not None:
        query = query.where(col(MessageEvent.provider_id) == provider_id)
    if label is not None:
        query = query.where(LABELS[label])
    at, key = col(MessageEvent.posted_at), col(MessageEvent.message_id)
    rows = (await db.execute(keyset(query, at, key, cursor, limit, int))).all()
    page, nxt = cut(rows, limit, lambda r: (r[0].posted_at, r[0].message_id))

    keys = [(m.message_id, m.event_type, m.event_seq) for m, _ in page]
    msg_types: defaultdict[tuple[int, str, int], list[str]] = defaultdict(list)
    card_ids: defaultdict[tuple[int, str, int], list[Any]] = defaultdict(list)
    if keys:
        signal_key = tuple_(col(Signal.message_id), col(Signal.event_type), col(Signal.event_seq))
        rows2 = await db.execute(
            select(Signal, col(Card.card_id))
            .outerjoin(Card, col(Card.signal_id) == col(Signal.signal_id))
            .where(signal_key.in_(keys))
            .order_by(col(Signal.idx))
        )
        seen = set()
        for signal, card_id in rows2.all():
            k = (signal.message_id, signal.event_type, signal.event_seq)
            if signal.signal_id not in seen:
                seen.add(signal.signal_id)
                msg_types[k].append(signal.msg_type)
            if card_id is not None:
                card_ids[k].append(card_id)
    return [
        _item(m, i, msg_types[k], card_ids[k]) for (m, i), k in zip(page, keys, strict=True)
    ], nxt


def _item(m: MessageEvent, inbox: InboxItem | None, types: list[str], cards: list[Any]) -> FeedItem:
    labels = []
    if inbox is not None:
        if inbox.status == InboxStatus.STALE:
            labels.append("stale")
        if inbox.late:
            labels.append("late")
        if inbox.skip_reason == SkipReason.MUTED_AUTHOR:
            labels.append("muted")
        if inbox.skip_reason == SkipReason.CHANNEL_NOT_CONFIGURED:
            labels.append("channel_not_configured")
        if inbox.status == InboxStatus.FAILED:
            labels.append("failed")
    if m.backfilled:
        labels.append("backfilled")
    return FeedItem(
        message_id=m.message_id,
        event_type=m.event_type,
        event_seq=m.event_seq,
        provider_id=m.provider_id,
        channel_id=m.channel_id,
        posted_at=m.posted_at,
        text=m.text,
        reply_to_id=m.reply_to_id,
        status=inbox.status if inbox else None,
        labels=labels,
        msg_types=types,
        card_ids=cards,
    )
