"""Discord message -> provisional payload -> Envelope."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS

from app.discord_source import mapping
from app.inbox.models import EventType
from app.ingest.adapter import to_envelope
from tests.messages import CHANNEL, PROVIDER_A, snowflake

AT = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
NOW = datetime(2026, 9, 28, 14, 31, tzinfo=UTC)


def message(content: str = "Bought GOOGL 357.5C @ 1.67", **over: object) -> NS:
    base = {
        "id": snowflake(AT),
        "channel": NS(id=CHANNEL),
        "author": NS(id=PROVIDER_A, bot=False),
        "content": content,
        "embeds": [],
        "reference": None,
        "mentions": [],
        "edited_at": None,
    }
    return NS(**{**base, **over})


def test_create_maps_to_envelope() -> None:
    reply = NS(message_id=snowflake(AT) - 1)
    payload = mapping.create_payload(
        message(reference=reply, mentions=[NS(id=PROVIDER_A + 1)])  # type: ignore[arg-type]
    )
    assert payload is not None
    env = to_envelope(payload, NOW)
    assert (env.key.event_type, env.provider_id, env.channel_id) == (
        EventType.CREATE,
        PROVIDER_A,
        CHANNEL,
    )
    assert env.text == "Bought GOOGL 357.5C @ 1.67"
    assert env.reply_to_id == snowflake(AT) - 1
    assert env.mentioned_author_ids == (PROVIDER_A + 1,)


def test_embed_text_is_appended() -> None:
    embed = NS(title="BTO SPY 600C", description="@ 2.10")
    payload = mapping.create_payload(message(content="", embeds=[embed]))
    assert payload is not None
    assert to_envelope(payload, NOW).text == "BTO SPY 600C\n@ 2.10"


def test_attachment_only_message_is_empty_text_not_unmappable() -> None:
    payload = mapping.create_payload(message(content=""))
    assert payload is not None
    assert to_envelope(payload, NOW).text == ""


def test_bot_authors_are_skipped() -> None:
    bot = message(author=NS(id=PROVIDER_A, bot=True))
    assert mapping.create_payload(bot) is None  # type: ignore[arg-type]
    assert mapping.edit_payload(bot) is None  # type: ignore[arg-type]


def test_edit_needs_a_text_edit_timestamp() -> None:
    assert mapping.edit_payload(message()) is None  # type: ignore[arg-type]  # embed unfurl
    edited = message("Bought GOOGL 357.5C @ 1.70", edited_at=NOW)
    payload = mapping.edit_payload(edited)  # type: ignore[arg-type]
    assert payload is not None
    env = to_envelope(payload, NOW)
    assert (env.key.event_type, env.key.event_seq) == (EventType.EDIT, int(NOW.timestamp() * 1000))
    assert env.text == "Bought GOOGL 357.5C @ 1.70"


def test_delete_maps_with_stored_author() -> None:
    payload = mapping.delete_payload(snowflake(AT), CHANNEL, PROVIDER_A, NOW)
    env = to_envelope(payload, NOW)
    assert (env.key.event_type, env.provider_id, env.text) == (EventType.DELETE, PROVIDER_A, None)


def test_thread_message_belongs_to_the_parent_channel() -> None:
    thread = NS(id=CHANNEL + 77, parent_id=CHANNEL)
    payload = mapping.create_payload(message(channel=thread, guild=NS(id=42)))
    assert payload is not None
    env = to_envelope(payload, NOW)
    assert (env.channel_id, env.thread_id, env.guild_id) == (CHANNEL, CHANNEL + 77, 42)


def test_plain_channel_has_no_thread() -> None:
    payload = mapping.create_payload(message())
    assert payload is not None
    env = to_envelope(payload, NOW)
    assert (env.channel_id, env.thread_id, env.guild_id) == (CHANNEL, None, None)
