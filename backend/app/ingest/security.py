"""Webhook authentication: HMAC-SHA256 over `<timestamp>.<raw body>` plus a replay window."""

import hashlib
import hmac
from datetime import datetime


def sign(secret: bytes, timestamp: int, body: bytes) -> str:
    return hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify(
    secret: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    body: bytes,
    now: datetime,
    max_skew_s: int,
) -> bool:
    """Constant-time check of `X-Signature`; `X-Timestamp` (unix s) must be within the window."""
    if not timestamp_header or not signature_header:
        return False
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        return False
    if abs(now.timestamp() - timestamp) > max_skew_s:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature_header)
