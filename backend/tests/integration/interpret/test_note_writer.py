"""F034 note writer: only note columns change (AC-13), hidden notes alert, no user data sent."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.cards import repo
from app.cards.models import Card, CardAction, CardSource, CardState, CardType
from app.config import Settings
from app.db import create_session_factory, in_transaction
from app.interpret.models import Signal
from app.interpret.note import NoteWriter
from app.interpret.signal import F, LiteralSpan, SignalFields
from app.llm.chain import LlmChain
from app.llm.models import ModelCall
from app.markettime import FixedClock
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmRoles, RoleConfig, RuntimeConfig
from tests.integration.conftest import _url
from tests.integration.schema.helpers import add_message
from tests.llm_helpers import FakeChat, FakeRegistry, structured

POSTED = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
TEXT = "Bought GOOGL 357.5C @ 1.67"  # add_signal's message
PROPOSAL = {
    "action": "entry",
    "underlying": "GOOGL",
    "expiry": "2026-10-02",
    "strike": "357.5",
    "right": "call",
    "limit_price": "1.67",
    "qty": 5,  # the user's quantity: never sent
    "breakeven_options": {"user_avg": "1.52", "provider_entry": "1.67"},
}


def lit(value: object, raw: str) -> F[object]:
    return F[object](value=value, source=LiteralSpan(raw=raw))


FIELDS = SignalFields(
    underlying=lit("googl", "GOOGL"),
    strike=lit(Decimal("357.5"), "357.5C"),
    right=lit("call", "357.5C"),
    entry_price=lit(Decimal("1.67"), "1.67"),
).model_dump(mode="json", exclude_defaults=True)


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def make_card(sessions: async_sessionmaker[AsyncSession], db: AsyncSession) -> uuid.UUID:
    await add_message(db, message_id=1001, provider_id=7, text=TEXT)
    signal = Signal(
        signal_id=uuid.uuid4(),
        message_id=1001,
        event_type="create",
        event_seq=0,
        idx=0,
        provider_id=7,
        msg_type="entry",
        instrument="option",
        fields=FIELDS,
        interpreter="llm",
        stale=False,
        late=False,
        schema_version=1,
    )
    db.add(signal)
    await db.commit()
    card = Card(
        dedupe_key=f"{signal.signal_id}:entry",
        source=CardSource.SIGNAL,
        signal_id=signal.signal_id,
        action=CardAction.ENTRY,
        card_type=CardType.NORMAL,
        state=CardState.PENDING,
        provider_id=7,
        proposal=PROPOSAL,
        posted_at=POSTED,
    )
    async with in_transaction(sessions, None) as tx:
        await repo.insert(tx, card)
    return card.card_id


def writer(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    note: str | None,
    guard: bool = True,
) -> NoteWriter:
    llm = LlmConfig(
        roles=LlmRoles(
            note=RoleConfig(
                deadline_ms=2000,
                chain=[ChainEntry(provider="deepseek", model="ds-test", prompt="note@v1")],
            )
        )
    )
    cfg = RuntimeConfig(llm=llm, note_guard_enabled=guard)
    replies = [structured("NoteOut", {"note": note})] if note else [structured("NoteOut", {})]
    chain = LlmChain(
        sessions,
        settings.model_copy(update={"deepseek_api_key": SecretStr("k")}),
        lambda: cfg.llm,
        FixedClock(POSTED),
        models=FakeRegistry({"deepseek": FakeChat(responses=replies)}),
    )
    return NoteWriter(sessions, chain, AlertService(sessions), lambda: cfg)


async def card_row(db: AsyncSession, card_id: uuid.UUID) -> Card:
    db.expire_all()
    card = await db.get(Card, card_id)
    assert card is not None
    return card


async def test_note_with_card_values_is_shown_and_nothing_else_changes(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    card_id = await make_card(sessions, db_session)
    before = await card_row(db_session, card_id)
    proposal, version = dict(before.proposal), before.version
    note = "Provider bought GOOGL 357.5C at 1.67, expiring 10/2."
    assert await writer(sessions, settings, note).write(card_id)
    after = await card_row(db_session, card_id)
    assert (after.note, after.note_hidden) == (note, False)
    assert after.proposal == proposal and after.version == version  # AC-13
    assert await db_session.scalar(select(Alert)) is None
    call = await db_session.scalar(select(ModelCall))
    assert call is not None and call.card_id == card_id and call.role == "note"
    sent = call.request["input"]
    assert "qty" not in sent["contract"] and "breakeven_options" not in sent["contract"]
    assert sent["source_messages"] == [TEXT]


async def test_note_naming_another_strike_is_hidden_and_alerted(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    card_id = await make_card(sessions, db_session)
    note = "Provider bought GOOGL 360C at 1.67."
    assert await writer(sessions, settings, note).write(card_id)
    after = await card_row(db_session, card_id)
    assert (after.note, after.note_hidden) == (note, True)  # stored for audit, not shown
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and alert.kind == "note_hidden" and alert.card_id == card_id
    assert alert.details["offending"] == ["360C"]


async def test_guard_disabled_shows_the_note_regardless(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    card_id = await make_card(sessions, db_session)
    assert await writer(sessions, settings, "GOOGL 360C", guard=False).write(card_id)
    assert (await card_row(db_session, card_id)).note_hidden is False


async def test_failed_chain_writes_no_note(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    card_id = await make_card(sessions, db_session)
    assert not await writer(sessions, settings, None).write(card_id)  # schema-invalid reply
    after = await card_row(db_session, card_id)
    assert after.note is None and after.note_hidden is False
