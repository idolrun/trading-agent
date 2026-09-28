"""Synthetic Message API payloads (provisional contract) and webhook signing for tests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ingest.security import sign
from app.markettime import DISCORD_EPOCH_MS

FIXTURES = Path(__file__).parent / "fixtures" / "messages"
PROVIDER_A, PROVIDER_B, CHANNEL = 700000000000000001, 800000000000000002, 900000000000000003


def snowflake(at: datetime, seq: int = 0) -> int:
    return ((int(at.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22) | seq


def create(
    at: datetime,
    text: str,
    author: int = PROVIDER_A,
    channel: int = CHANNEL,
    seq: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": "message.create",
        "message": {
            "id": str(snowflake(at, seq)),
            "channel_id": str(channel),
            "author": {"id": str(author)},
            "content": text,
            "timestamp": at.isoformat(),
            **extra,
        },
    }


def ac18() -> dict[str, dict[str, Any]]:
    """AC-18 set: A1, B2, A3, B4 (label -> payload)."""
    items = json.loads((FIXTURES / "ac18_take_half.json").read_text())
    return {item["label"]: item["payload"] for item in items}


def signed(
    payload: object, secret: str, at: datetime | None = None
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    ts = int((at or datetime.now(UTC)).timestamp())
    return body, {
        "X-Timestamp": str(ts),
        "X-Signature": sign(secret.encode(), ts, body),
        "Content-Type": "application/json",
    }
