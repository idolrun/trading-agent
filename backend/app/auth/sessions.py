"""Session rules (pure): lifetime, device binding, step-up window."""

import hashlib
import secrets
from datetime import datetime, timedelta

from app.auth.models import AuthSession
from app.config import Settings


def new_token() -> str:
    """Random 256-bit token for a cookie or enrolment; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def session_problem(
    row: AuthSession, now: datetime, user_agent: str | None, settings: Settings
) -> str | None:
    """Why the session can no longer be used, or None if it is valid.

    OD-23 proposal: "device-bound" = bound to its credential and User-Agent; a changed
    User-Agent revokes the session.
    """
    if row.revoked_at is not None:
        return "revoked"
    if now >= row.expires_at:
        return "expired"
    if now - row.last_seen_at > timedelta(minutes=settings.session_idle_minutes):
        return "idle"
    if row.user_agent != user_agent:
        return "device_changed"
    return None


def step_up_since(now: datetime, settings: Settings) -> datetime:
    """Oldest step-up time still accepted."""
    return now - timedelta(seconds=settings.step_up_window_s)
