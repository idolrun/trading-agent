"""AC-14 / FR-14: a missing underlying is a review card; confirming the suggestion re-plans it
into a tradable entry on that underlying and opens the provider position it skipped."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.cards.approval_service import DecisionInvalid
from app.cards.models import Card, CardAction, CardState, CardType
from app.decide.types import ProposedAction
from app.interpret.models import MessageLink
from app.interpret.signal import ParsedSignal, SignalFields
from app.ledger.models import ProviderPosition
from app.pipeline.apply import signal_id_for
from tests.integration.graph.conftest import World
from tests.integration.pipeline.harness import (
    T0,
    follow_up,
    lit,
    opened_position,
    option_entry,
)
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph


def no_underlying(strike: str, right_raw: str, expiry: str, price: str) -> ParsedSignal:
    return ParsedSignal(
        msg_type="entry",
        instrument="option",
        fields=SignalFields(
            strike=lit(Decimal(strike), strike),
            right=lit("call", right_raw),
            expiry_token=lit(expiry),
            entry_price=lit(Decimal(price), price),
        ),
    )


async def card_of(w: World, action: CardAction, state: CardState) -> Card:
    async with w.h.factory() as db:
        card = await db.scalar(
            select(Card)
            .where(col(Card.action) == action, col(Card.state) == state)
            .order_by(col(Card.created_at).desc())
        )
    assert card is not None, (action, state)
    return card


async def test_confirmed_underlying_makes_the_review_card_tradable(w: World) -> None:
    # the provider traded MU this week: MU (118.70) is the one candidate near a 118 strike
    earlier = await w.h.message(
        "Bought MU 118C 10/2 @ 0.94",
        [option_entry("MU", "118", "118C", "10/2", "0.94")],
        at=T0,
        author=PROVIDER_A,
    )
    await w.h.process(earlier, processor=w.processor)
    await w.decide((await card_of(w, CardAction.ENTRY, CardState.PENDING)).card_id, "reject")

    env = await w.h.message(
        "118c 10/2 @ 0.94",
        [no_underlying("118", "118c", "10/2", "0.94")],
        at=T0 + timedelta(seconds=2),
        author=PROVIDER_A,
    )
    await w.h.process(env, processor=w.processor)
    review = await card_of(w, CardAction.ENTRY, CardState.PENDING)
    proposal = ProposedAction.model_validate(review.proposal)
    assert review.card_type == CardType.REVIEW and review.provider_position_id is None
    assert proposal.missing_fields == ["underlying_confirmation"]
    assert proposal.suggested_underlying == "MU" and proposal.contract is None
    position_id = opened_position(env)
    async with w.h.factory() as db:
        assert await db.get(ProviderPosition, position_id) is None  # not opened while unknown

    await w.decide(review.card_id, "edit", 1, confirmed_underlying="MU")

    card = await w.card(review.card_id)
    confirmed = ProposedAction.model_validate(card.proposal)
    assert (card.state, card.version, card.card_type) == (
        CardState.PENDING,
        2,
        CardType.NORMAL,
    )
    assert confirmed.missing_fields == [] and confirmed.review_reasons == []
    assert confirmed.qty is None and confirmed.limit_price is not None  # the user types the qty
    assert confirmed.valid_until == proposal.valid_until  # the expiry timer's window
    assert card.provider_position_id == position_id
    async with w.h.factory() as db:
        position = await db.get(ProviderPosition, position_id)
    assert position is not None and (position.underlying, position.strike) == (
        "MU",
        Decimal("118"),
    )

    with pytest.raises(DecisionInvalid):  # confirmed once: no second re-plan or position
        await w.decide(review.card_id, "edit", 2, confirmed_underlying="MU")

    await w.decide(review.card_id, "approve", 2)
    [order] = [o for o in await w.orders() if o.card_id == review.card_id]
    assert "MU" in (order.broker_symbol or "")
    assert (await w.card(review.card_id)).state == CardState.SUBMITTED
    await w.consume()

    # the provider's follow-up: two MU 118C positions are live (the rejected first entry still
    # opened the provider's), so "trim half" names neither: review, the model's pick is a
    # proposal only (F077)
    trim = await w.h.message(
        "trim half",
        [follow_up("trim", position_id, exit_fraction="half")],
        at=T0 + timedelta(seconds=4),
        author=PROVIDER_A,
    )
    await w.h.process(trim, processor=w.processor)
    async with w.h.factory() as db:
        position = await db.get(ProviderPosition, position_id)
        link = await db.scalar(
            select(MessageLink).where(col(MessageLink.signal_id) == signal_id_for(trim.key, 0))
        )
    assert position is not None and position.remaining_fraction == 1
    assert link is not None and (link.outcome, link.provider_position_id) == ("ambiguous", None)
    assert position_id in link.candidate_position_ids and len(link.candidate_position_ids) == 2
    assert link.evidence is not None and link.evidence["rule"] == "model_proposed"
    assert link.evidence["proposal"]["target"] == str(position_id)
