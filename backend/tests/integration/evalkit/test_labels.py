"""F048 labels: the supersede chain, F047's label shape, and the prelabel batch."""

import uuid
from datetime import datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.labels import Label
from app.config import get_settings
from app.db import in_transaction
from app.evalkit.labels import (
    LabelConflict,
    add_label,
    event_key,
    history,
    latest_labels,
    parse_event_key,
    pending_prelabel,
    prelabel_batch,
)
from app.evalkit.models import LabelSource, Split
from app.inbox import repo as inbox
from app.inbox.envelope import EventKey
from app.inbox.models import EventType
from app.ingest.adapter import to_envelope
from app.llm.chain import LlmChain
from app.llm.models import ModelCall
from app.markettime import FixedClock
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmRoles, RoleConfig
from tests.integration.evalkit.conftest import (
    CHATTER_LABEL,
    GOOGL_LABEL,
    GOOGL_TEXT,
    Corpus,
    at,
    day_in,
)
from tests.integration.pipeline.harness import Factory, Harness
from tests.llm_helpers import FakeChat, FakeRegistry, structured
from tests.messages import PROVIDER_A, create

TEST_DAY = day_in(Split.TEST)


def test_event_key_round_trips() -> None:
    key = EventKey(123, EventType.EDIT, 1_790_000_000_000)
    assert event_key(key) == "123.edit.1790000000000"
    assert parse_event_key(event_key(key)) == key
    for bad in ("123", "x.create.0", "1.nope.0", "1.create.0.9"):
        with pytest.raises(ValueError):
            parse_event_key(bad)


async def test_a_new_label_supersedes_the_current_one(
    h: Harness, corpus: Corpus, db_session: AsyncSession
) -> None:
    env = await corpus.add(GOOGL_TEXT, [], [CHATTER_LABEL], when=at(TEST_DAY))
    async with in_transaction(h.factory, None) as db:
        second = await add_label(
            db,
            env.key,
            {"signals": [{"idx": 0, **GOOGL_LABEL}]},
            source=LabelSource.MANUAL,
            session_id=None,
        )
        [first] = [r for r in await history(db, env.key) if r.label_id != second.label_id]
    assert second.supersedes_label_id == first.label_id
    assert (second.split, second.trading_day) == (Split.TEST, TEST_DAY)
    async with h.factory() as db:
        [current] = await latest_labels(db)
        with pytest.raises(LabelConflict):  # `first` is no longer current
            await add_label(
                db,
                env.key,
                {"signals": []},
                source=LabelSource.MANUAL,
                session_id=None,
                supersedes=first.label_id,
            )
    assert current.label_id == second.label_id


async def test_f047_correction_labels_stay_readable() -> None:
    label = {
        "signals": [{"idx": 0, "msg_type": "entry", "corrected": {"strike": "360"}}],
        "link": {"idx": 0, "provider_position_id": str(uuid.uuid4())},
    }
    stored = Label.model_validate(label).model_dump(by_alias=False, mode="json", exclude_none=True)
    assert stored == {**label, "signals": [{**label["signals"][0], "fields": {}}]}
    assert (
        Label.model_validate({"signals": [{"idx": 0, "msg_type": "entry"}], "link": None}).link
        is None
    )


async def test_prelabel_suggests_for_unlabeled_events_only(
    h: Harness, corpus: Corpus, factory: Factory, db_session: AsyncSession
) -> None:
    await corpus.add("gm", [], [CHATTER_LABEL], when=at(TEST_DAY, 1))  # already labeled
    env = to_envelope(create(at(TEST_DAY, 2), GOOGL_TEXT, author=PROVIDER_A), at(TEST_DAY, 2))
    async with in_transaction(factory, None) as db:
        await inbox.store_event(db, env)
    answer = {
        "signals": [
            {
                "idx": 0,
                "msgType": "entry",
                "instrument": "option",
                "fields": GOOGL_LABEL["fields"],
            }
        ]
    }
    cfg = LlmConfig(
        roles=LlmRoles(
            prelabel=RoleConfig(
                chain=[ChainEntry(provider="deepseek", model="ds-test", prompt="prelabel@v1")]
            )
        )
    )
    chain = LlmChain(
        factory,
        get_settings().model_copy(update={"deepseek_api_key": SecretStr("k")}),
        lambda: cfg,
        FixedClock(datetime(2026, 9, 29, tzinfo=at(TEST_DAY).tzinfo)),
        models=FakeRegistry({"deepseek": FakeChat(responses=[structured("Label", answer)])}),
    )
    assert await prelabel_batch(factory, chain, limit=10, recent_n=5) == 1
    assert await prelabel_batch(factory, chain, limit=10, recent_n=5) == 0  # nothing left to ask
    [call] = (await db_session.scalars(select(ModelCall))).all()
    assert (call.role, call.outcome, call.message_id) == ("prelabel", "ok", env.key.message_id)
    assert "lots" not in str(call.request).lower()  # message and provider context only (C87)
    async with factory() as db:
        suggestion = await pending_prelabel(db, env.key)
    assert suggestion is not None and suggestion.signals[0].fields["strike"] == "357.5"
    assert call.response == suggestion.model_dump(mode="json", by_alias=False, exclude_none=True)
