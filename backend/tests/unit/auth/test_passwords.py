import asyncio

from app.auth.passwords import (
    hash_password,
    temporary_password,
    verify_password,
    verify_password_async,
)


def test_round_trip_and_salting() -> None:
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b and a.startswith("scrypt$")
    assert verify_password("correct horse", a) and verify_password("correct horse", b)
    assert not verify_password("Correct horse", a)


def test_malformed_hash_is_rejected() -> None:
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "bcrypt$1$2$3$4$5")


def test_unknown_user_never_verifies() -> None:
    assert asyncio.run(verify_password_async("anything", None)) is False


def test_temporary_passwords_differ() -> None:
    assert len({temporary_password() for _ in range(50)}) == 50
    assert len(temporary_password()) >= 16
