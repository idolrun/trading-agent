"""Structured JSON logging with secret redaction and contextvar-bound correlation IDs."""

import contextvars
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretBytes, SecretStr

REDACTED = "***"
_SENSITIVE_KEY = re.compile(r"(?i)(key|token|secret|authorization|cookie|password)")
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("log_context", default={})  # noqa: B039

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def _redact(value: Any) -> Any:
    if isinstance(value, SecretStr | SecretBytes):
        return REDACTED
    if isinstance(value, dict):
        return {
            k: REDACTED if _SENSITIVE_KEY.search(str(k)) else _redact(v) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_context.get(),
            **extra,
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(_redact(entry), default=str)


class RedactionFilter(logging.Filter):
    """Masks secret values in args before the message is rendered."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, dict):
            record.args = _redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact(a) for a in record.args)
        return True


@contextmanager
def bind(**ids: Any) -> Iterator[None]:
    """Attach correlation IDs (message_id, signal_id, card_id …) to every log line in scope."""
    token = _context.set({**_context.get(), **ids})
    try:
        yield
    finally:
        _context.reset(token)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
