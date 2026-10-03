"""F075: a person resolving an ambiguous card writes the link and the ledger change, so the
next follow-up sees the corrected state (before: only the card changed)."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.cards.models import Card, CardAction, CardState, CardType
from app.interpret.models import LinkMethod, MessageLink
from app.ledger.models import PositionStatus, ProviderPosition
from app.pipeline.apply import signal_id_for
from tests.integration.graph.conftest import World
from tests.integration.pipeline.harness import T0, follow_up, opened_position, option_entry
from tests.messages import PROVIDER_A

S = timedelta(seconds=1)


async def test_chosen_position_is_persisted_and_used_by_the_next_reply(w: World) -> None:
    h = w.h
    spy = await h.message(
        "SPY 580p 9/28 2.30",
        [option_entry("SPY", "580", "580p", "9/28", "2.30")],
        at=T0,
        author=PROVIDER_A,
    )
    qqq = await h.message(
        "QQQ 495p 9/28 .98",
        [option_entry("QQQ", "495", "495p", "9/28", ".98")],
        at=T0 + S,
        author=PROVIDER_A,
    )
    trim = await h.message(
        "trim half",
        [follow_up("trim", "ambiguous", conf="0.5", exit_fraction="half")],
        at=T0 + 10 * S,
        author=PROVIDER_A,
    )
    for env in (spy, qqq, trim):
        await h.process(env, processor=w.processor)
    p_spy, p_qqq = opened_position(spy), opened_position(qqq)

    async with h.factory() as db:
        card = await db.scalar(select(Card).where(Card.signal_id == signal_id_for(trim.key, 0)))
    assert card is not None and card.card_type == CardType.REVIEW
    assert card.proposal["missing_fields"] == ["chosen_position_id"]

    await w.decide(card.card_id, "edit", 1, chosen_position_id=p_qqq)
    edited = await w.card(card.card_id)
    assert edited.proposal["relink_signal_id"] == str(signal_id_for(trim.key, 0))
    await w.decide(card.card_id, "approve", edited.version)

    async with h.factory() as db:
        link = await db.scalar(
            select(MessageLink)
            .where(MessageLink.signal_id == signal_id_for(trim.key, 0))
            .order_by(MessageLink.id.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        q = await db.get(ProviderPosition, p_qqq)
        s = await db.get(ProviderPosition, p_spy)
    assert link is not None
    assert (link.method, link.provider_position_id) == (LinkMethod.USER_CORRECTION, p_qqq)
    assert q is not None and (q.status, q.remaining_fraction) == (
        PositionStatus.TRIMMED,
        Decimal("0.5"),
    )
    assert s is not None and s.remaining_fraction == 1
    assert (await w.card(card.card_id)).state == CardState.APPROVED
    assert await w.orders() == []  # resolving the ambiguity places nothing

    # the next follow-up replies to the trim: the persisted link resolves it (reply rung)
    out = await h.message(
        "out",
        [follow_up("full_exit", "ambiguous", conf="0.3", exit_fraction="all")],
        at=T0 + 20 * S,
        author=PROVIDER_A,
        reply_to_id=str(trim.key.message_id),
    )
    await h.process(out, processor=w.processor)
    async with h.factory() as db:
        q = await db.get(ProviderPosition, p_qqq)
        out_link = await db.scalar(
            select(MessageLink).where(MessageLink.signal_id == signal_id_for(out.key, 0))
        )
        trim_cards = (await db.scalars(select(Card).where(Card.action == CardAction.TRIM))).all()
    assert out_link is not None and (out_link.method, out_link.provider_position_id) == (
        LinkMethod.REPLY,
        p_qqq,
    )
    assert q is not None and q.status == PositionStatus.CLOSED
    assert len(trim_cards) == 1  # the original review card only; no lot, so no new trim card
