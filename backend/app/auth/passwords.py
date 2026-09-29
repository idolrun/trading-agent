"""Password hashing (stdlib scrypt) and temporary-password generation. Hashing is CPU-bound, so
the async wrappers run it in a thread."""

import asyncio
import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1
MIN_LENGTH = 12


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, _N, _R, _P)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    except ValueError:  # malformed hash or unusable parameters
        return False
    return hmac.compare_digest(actual, expected)


# Verified against when the email is unknown, so timing does not reveal which emails exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, stored: str | None) -> bool:
    matches = await asyncio.to_thread(verify_password, password, stored or _DUMMY_HASH)
    return matches and stored is not None


def temporary_password() -> str:
    return secrets.token_urlsafe(12)
