from datetime import UTC, datetime, timedelta

import pytest

from app.inbox.models import EventType
from app.ingest.adapter import UnmappablePayload, to_envelope
from tests.messages import CHANNEL, PROVIDER_A, create, snowflake

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
NOW = T0 + timedelta(seconds=1)


def test_create_uses_snowflake_time() -> None:
    payload = create(T0, "Bought GOOGL 357.5C @ 1.67", reply_to_id="123", mentions=["456"])
    env = to_envelope(payload, NOW)
    assert env.key == (snowflake(T0), EventType.CREATE, 0)
    assert env.posted_at == T0
    assert (env.provider_id, env.channel_id) == (PROVIDER_A, CHANNEL)
    assert env.reply_to_id == 123 and env.mentioned_author_ids == (456,)
    assert env.text == "Bought GOOGL 357.5C @ 1.67"
    assert env.payload == payload and env.received_at == NOW and not env.backfilled


def test_edit_is_sequenced_at_edit_time() -> None:
    edited = T0 + timedelta(minutes=3, milliseconds=250)
    payload = create(T0, "Bought GOOGL 355C @ 1.67", edited_timestamp=edited.isoformat())
    payload["type"] = "message.update"
    env = to_envelope(payload, NOW)
    assert env.key.event_type is EventType.EDIT
    assert env.key.event_seq == int(edited.timestamp() * 1000)
    assert env.posted_at == env.edited_at == edited


def test_delete_has_no_text_and_uses_delete_time() -> None:
    payload = create(T0, "ignored")
    payload["type"] = "message.delete"
    env = to_envelope(payload, NOW)
    assert (env.key.event_type, env.text, env.posted_at) == (EventType.DELETE, None, NOW)
    deleted = T0 + timedelta(minutes=5)
    payload["message"]["deleted_timestamp"] = deleted.isoformat()
    assert to_envelope(payload, NOW).posted_at == deleted


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(type="message.pin"),
        lambda p: p["message"].pop("author"),
        lambda p: p["message"].update(id="0"),
        lambda p: p["message"].update(content=None),  # create without text
        lambda p: p.update(type="message.update"),  # edit without edited_timestamp
        lambda p: (
            p["message"].update(edited_timestamp="2026-09-28T14:35:00", content="x")
            or p.update(type="message.update")
        ),  # naive timestamp
    ],
)
def test_unmappable(mutate) -> None:  # type: ignore[no-untyped-def]
    payload = create(T0, "hi")
    mutate(payload)
    with pytest.raises(UnmappablePayload):
        to_envelope(payload, NOW)
