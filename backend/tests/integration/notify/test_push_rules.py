"""F029: the FR-26 push matrix; disabled cards never push; 410 revokes the subscription."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pywebpush import WebPushException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.cards import repo as cards
from app.cards.models import Card, CardAction, CardSource, CardState, CardType
from app.config import get_settings
from app.db import create_session_factory, in_transaction
from app.notify.bus import Event, EventBus
from app.notify.models import PushSubscription
from app.notify.push import PushService, should_push
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import Factory
from tests.integration.schema.helpers import add_signal


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def test_matrix() -> None:
    assert should_push("card.created", card_state="pending")
    assert not should_push("card.created", card_state="disabled")  # C75
    assert should_push("fill.created")
    assert should_push("order.created", auto_reason="eod_forced_close")
    assert not should_push("order.created", auto_reason="reprice")
    assert not should_push("order.created", auto_reason=None)
    assert should_push("alert.created", alert_severity="urgent")
    assert should_push("alert.created", alert_severity="info", alert_kind="risk_limit")
    assert not should_push("alert.created", alert_severity="info", alert_kind="catch_up")
    assert not should_push("card.updated", card_state="pending")


async def add_card(db_session: AsyncSession, factory: Factory, state: CardState) -> uuid.UUID:
    sig = await add_signal(db_session, message_id=1000 + len(state))
    key = f"{sig.signal_id}:entry"
    async with in_transaction(factory, None) as db:
        await cards.insert(
            db,
            Card(
                card_id=cards.card_id_for(key),
                dedupe_key=key,
                source=CardSource.SIGNAL,
                signal_id=sig.signal_id,
                action=CardAction.ENTRY,
                card_type=CardType.DISABLED if state == CardState.DISABLED else CardType.NORMAL,
                state=state,
                provider_id=7,
                proposal={},
                posted_at=datetime.now(UTC),
                disabled_reason="max exposure" if state == CardState.DISABLED else None,
            ),
        )
    return cards.card_id_for(key)


async def test_pending_pushes_disabled_does_not_and_gone_is_revoked(
    factory: Factory, db_session: AsyncSession
) -> None:
    async with in_transaction(factory, None) as db:
        for endpoint in ("https://push.example/ok", "https://push.example/gone"):
            db.add(PushSubscription(endpoint=endpoint, p256dh="k", auth="a"))
    sent: list[str] = []

    def sender(info: dict[str, Any], data: str) -> None:
        if info["endpoint"].endswith("gone"):
            raise WebPushException("gone", response=SimpleNamespace(status_code=410))
        sent.append(data)

    push = PushService(factory, EventBus(), get_settings(), sender=sender)
    pending = await add_card(db_session, factory, CardState.PENDING)
    disabled = await add_card(db_session, factory, CardState.DISABLED)
    assert await push.handle(Event("card.created", str(disabled))) == 0
    assert await push.handle(Event("card.created", str(pending))) == 1
    assert len(sent) == 1
    subs = {s.endpoint: s for s in (await db_session.scalars(select(PushSubscription))).all()}
    assert subs["https://push.example/gone"].revoked_at is not None
    assert subs["https://push.example/ok"].revoked_at is None
    assert await push.handle(Event("card.created", str(pending))) == 1  # revoked one skipped
