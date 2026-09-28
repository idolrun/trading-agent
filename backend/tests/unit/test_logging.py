import json
import logging

from pydantic import SecretStr

from app.api.schemas.base import CamelModel, DiscordId
from app.logging import JsonFormatter, RedactionFilter, bind


def _render(record: logging.LogRecord) -> str:
    RedactionFilter().filter(record)
    return JsonFormatter().format(record)


def _record(msg: str, args: object = None, **extra: object) -> logging.LogRecord:
    r = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)  # type: ignore[arg-type]
    r.__dict__.update(extra)
    return r


def test_secretstr_arg_never_rendered() -> None:
    out = _render(_record("key=%s", (SecretStr("sk-live-123"),)))
    assert "sk-live-123" not in out


def test_raw_secret_value_in_secretstr_extra_redacted() -> None:
    out = _render(_record("x", api=SecretStr("sk-live-123")))
    assert "sk-live-123" not in out


def test_sensitive_keys_redacted() -> None:
    headers = {"Authorization": "Bearer abc", "Cookie": "sid=xyz", "Accept": "json"}
    out = json.loads(_render(_record("req", headers=headers)))
    assert out["headers"] == {"Authorization": "***", "Cookie": "***", "Accept": "json"}


def test_bind_adds_correlation_ids() -> None:
    with bind(message_id=42):
        out = json.loads(_render(_record("hi")))
    assert out["message_id"] == 42
    assert "message_id" not in json.loads(_render(_record("hi")))


class _Card(CamelModel):
    provider_id: DiscordId
    card_type: str


def test_camel_model_dumps_camel_case_and_id_as_string() -> None:
    card = _Card(provider_id=1234567890123456789, card_type="normal")
    assert card.model_dump(mode="json") == {
        "providerId": "1234567890123456789",
        "cardType": "normal",
    }
    assert _Card.model_validate({"providerId": 5, "cardType": "x"}).provider_id == 5
