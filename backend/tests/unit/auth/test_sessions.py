import uuid
from datetime import UTC, datetime, timedelta

from app.auth.models import AuthSession
from app.auth.rate_limit import SlidingWindowLimiter
from app.auth.sessions import hash_token, new_token, session_problem, step_up_since
from app.config import Settings
from app.markettime import FixedClock

T0 = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
SETTINGS = Settings.model_construct(
    session_idle_minutes=60, session_absolute_hours=12, step_up_window_s=120
)


def row(**overrides: object) -> AuthSession:
    values: dict[str, object] = {
        "session_id": uuid.uuid4(),
        "token_hash": b"h",
        "user_id": uuid.uuid4(),
        "created_at": T0,
        "expires_at": T0 + timedelta(hours=12),
        "last_seen_at": T0,
        "user_agent": "phone",
        "revoked_at": None,
    }
    return AuthSession(**(values | overrides))


def test_valid_session() -> None:
    assert session_problem(row(), T0 + timedelta(minutes=59), "phone", SETTINGS) is None


def test_idle_timeout() -> None:
    assert session_problem(row(), T0 + timedelta(minutes=61), "phone", SETTINGS) == "idle"


def test_absolute_timeout_even_when_active() -> None:
    active = row(last_seen_at=T0 + timedelta(hours=12))
    assert session_problem(active, T0 + timedelta(hours=12), "phone", SETTINGS) == "expired"


def test_revoked() -> None:
    assert session_problem(row(revoked_at=T0), T0, "phone", SETTINGS) == "revoked"


def test_device_bound_to_user_agent() -> None:
    assert session_problem(row(), T0, "laptop", SETTINGS) == "device_changed"


def test_step_up_window() -> None:
    assert step_up_since(T0, SETTINGS) == T0 - timedelta(seconds=120)


def test_tokens_are_random_and_hashed() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43
    assert hash_token(a) != a.encode() and len(hash_token(a)) == 32


def test_sliding_window_limiter() -> None:
    clock = FixedClock(T0)
    limiter = SlidingWindowLimiter(3, timedelta(minutes=1), clock)
    assert [limiter.allow("ip1") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("ip2")  # per key
    clock.advance(timedelta(seconds=61))
    assert limiter.allow("ip1")  # window slid
