"""F017/F068: lane-scoped context (C71) and AC-18 isolation."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory, in_transaction
from app.inbox import repo as inbox
from app.inbox.envelope import Envelope
from app.ingest.adapter import to_envelope
from app.interpret.context import ProviderContext, load_context
from app.ledger.models import ProviderPosition
from tests.integration.conftest import _url
from tests.messages import CHANNEL, PROVIDER_A, PROVIDER_B, ac18, create

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
NOW = T0 + timedelta(minutes=5)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def deliver(factory: Factory, *payloads: dict[str, Any]) -> list[Envelope]:
    envs = []
    for payload in payloads:
        env = to_envelope(payload, NOW)
        async with in_transaction(factory, None) as db:
            await inbox.accept(db, env)
        envs.append(env)
    return envs


async def context(factory: Factory, env: Envelope, recent_n: int = 10) -> ProviderContext:
    async with factory() as db:
        return await load_context(db, env, recent_n=recent_n, now=NOW)


async def open_positions(factory: Factory) -> None:
    async with in_transaction(factory, None) as db:
        db.add_all(
            [
                ProviderPosition(
                    provider_id=PROVIDER_A,
                    opened_channel_id=CHANNEL,
                    instrument="option",
                    underlying="GOOGL",
                    expiry=date(2026, 10, 2),
                    strike=Decimal("357.5"),
                    option_right="call",
                    provider_entry_price=Decimal("1.67"),
                    status="open",
                    opened_at=T0,
                ),
                ProviderPosition(
                    provider_id=PROVIDER_B,
                    opened_channel_id=CHANNEL,
                    instrument="option",
                    underlying="TSLA",
                    expiry=date(2026, 10, 2),
                    strike=Decimal(400),
                    option_right="call",
                    provider_entry_price=Decimal("2.10"),
                    status="trimmed",
                    remaining_fraction=Decimal("0.5"),
                    stop_level=Decimal("1.50"),
                    stop_basis="premium",
                    tp_levels=[Decimal("2.8"), Decimal("3.5")],
                    opened_at=T0 + timedelta(seconds=10),
                ),
            ]
        )


async def test_ac18_contexts_are_isolated(factory: Factory) -> None:
    msgs = ac18()
    _a1, _b2, a3, b4 = await deliver(factory, msgs["A1"], msgs["B2"], msgs["A3"], msgs["B4"])
    await open_positions(factory)

    ctx_a3 = await context(factory, a3)
    assert [m.text for m in ctx_a3.recent_author_messages] == ["Bought GOOGL 357.5C @ 1.67"]
    assert [b.summary for b in ctx_a3.provider_book] == [
        "GOOGL 357.5C exp 2026-10-02, entered 1.67 at 2026-09-28 10:30:00 ET"
    ]
    ctx_b4 = await context(factory, b4)
    assert [m.text for m in ctx_b4.recent_author_messages] == ["Bought TSLA 400C @ 2.10"]
    assert [b.summary for b in ctx_b4.provider_book] == [
        "TSLA 400C exp 2026-10-02, entered 2.1 at 2026-09-28 10:30:10 ET, trimmed, "
        "50% remaining, stop 1.5 (premium), TPs 2.8/3.5"
    ]
    dumped = ctx_b4.model_dump_json()
    assert str(PROVIDER_A) not in dumped and "GOOGL" not in dumped
    assert ctx_a3.message.text == "Take half off @ 2.10" and ctx_a3.reply_parent is None


async def test_reply_to_other_author_is_read_only_context(factory: Factory) -> None:
    msgs = ac18()
    (a1,) = await deliver(factory, msgs["A1"])
    await open_positions(factory)
    (reply,) = await deliver(
        factory,
        create(
            T0 + timedelta(seconds=50),
            "same here",
            author=PROVIDER_B,
            reply_to_id=str(a1.key.message_id),
        ),
    )
    ctx = await context(factory, reply)
    assert ctx.reply_parent is not None
    assert (ctx.reply_parent.author_id, ctx.reply_parent.text) == (
        str(PROVIDER_A),
        "Bought GOOGL 357.5C @ 1.67",
    )
    assert [b.summary.split()[0] for b in ctx.provider_book] == ["TSLA"]  # B's book only


async def test_mentions_bring_no_other_authors_messages(factory: Factory) -> None:
    """F068: an @mention is not context; only the lane author's own messages are."""
    msgs = ac18()
    await deliver(factory, msgs["A1"], msgs["B2"])
    (mention,) = await deliver(
        factory,
        create(
            T0 + timedelta(seconds=40), "following A", author=PROVIDER_B, mentions=[str(PROVIDER_A)]
        ),
    )
    ctx = await context(factory, mention)
    assert [m.text for m in ctx.recent_author_messages] == ["Bought TSLA 400C @ 2.10"]
    assert "GOOGL" not in ctx.model_dump_json()


async def test_context_is_per_channel(factory: Factory) -> None:
    """F068: the same author in another channel is another lane: no messages, no book."""
    other = CHANNEL + 1
    await deliver(
        factory,
        create(T0, "SPY 765P .54"),
        create(T0 + timedelta(seconds=5), "QQQ 745P .60", channel=other),
    )
    await open_positions(factory)
    (trim,) = await deliver(factory, create(T0 + timedelta(seconds=30), "trim half", channel=other))
    ctx = await context(factory, trim)
    assert [m.text for m in ctx.recent_author_messages] == ["QQQ 745P .60"]
    assert ctx.provider_book == []  # GOOGL was opened in CHANNEL
    (same,) = await deliver(factory, create(T0 + timedelta(seconds=40), "trim half"))
    ctx = await context(factory, same)
    assert [m.text for m in ctx.recent_author_messages] == ["SPY 765P .54"]
    assert [b.summary.split()[0] for b in ctx.provider_book] == ["GOOGL"]


async def test_post_time_cutoff_edits_and_deletes(factory: Factory) -> None:
    first = create(T0, "first")
    second = create(T0 + timedelta(seconds=20), "second")
    later = create(T0 + timedelta(seconds=60), "later")
    edit = create(T0, "first (edited)", edited_timestamp=(T0 + timedelta(seconds=10)).isoformat())
    edit["type"] = "message.update"
    delete = create(T0 + timedelta(seconds=20), "")
    delete["type"] = "message.delete"
    delete["message"]["deleted_timestamp"] = (T0 + timedelta(seconds=30)).isoformat()
    # arrival order differs from post order: `later` is stored first
    _, _, current, *_ = await deliver(
        factory, later, first, create(T0 + timedelta(seconds=40), "now"), second, edit, delete
    )
    ctx = await context(factory, current)
    # `later` posted after the current message; `second` deleted before it
    assert [m.text for m in ctx.recent_author_messages] == ["first (edited)"]
    assert [
        m.text for m in (await context(factory, current, recent_n=0)).recent_author_messages
    ] == []
