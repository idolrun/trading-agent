from datetime import UTC, datetime, timedelta

from app.ingest.security import sign, verify

SECRET = b"s3cret"
NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
TS = int(NOW.timestamp())
BODY = b'{"type":"message.create"}'


def check(ts: object = TS, sig: object = None, body: bytes = BODY, now: datetime = NOW) -> bool:
    signature = sig if sig is not None else sign(SECRET, TS, BODY)
    return verify(SECRET, str(ts) if ts is not None else None, signature, body, now, 300)  # type: ignore[arg-type]


def test_valid() -> None:
    assert check()


def test_tampered_body_or_signature() -> None:
    assert not check(body=BODY + b" ")
    assert not check(sig="0" * 64)
    assert not check(sig="")


def test_timestamp_window() -> None:
    assert check(now=NOW + timedelta(seconds=300))
    assert not check(now=NOW + timedelta(seconds=301))
    assert not check(now=NOW - timedelta(seconds=301))
    assert not check(ts="not-a-number")
    assert not check(ts=None)


def test_signature_binds_timestamp() -> None:
    assert not check(ts=TS + 1, now=NOW)  # replayed body with a fresh timestamp
