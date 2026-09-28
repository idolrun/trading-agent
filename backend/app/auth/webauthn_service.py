"""WebAuthn options and verification (wraps py_webauthn). No HTTP or cookies here."""

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.markettime import Clock

USER_ID = b"trading-agent-owner"  # single user (C1)
USER_NAME = "owner"


class VerificationFailed(Exception):
    pass


class SignCountRegression(VerificationFailed):
    """Authenticator reported a sign count not above the stored one: possibly cloned."""


@dataclass(frozen=True)
class Registered:
    credential_id: bytes
    public_key: bytes
    sign_count: int


class WebAuthn:
    def __init__(self, rp_id: str, rp_name: str, origin: str) -> None:
        self.rp_id = rp_id
        self.rp_name = rp_name
        self.origin = origin

    def registration_options(self, existing: list[bytes]) -> tuple[dict[str, Any], bytes]:
        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            user_id=USER_ID,
            user_name=USER_NAME,
            exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        return json.loads(options_to_json(options)), options.challenge

    def verify_registration(self, credential: dict[str, Any], challenge: bytes) -> Registered:
        try:
            result = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
            raise VerificationFailed(str(exc)) from exc
        return Registered(result.credential_id, result.credential_public_key, result.sign_count)

    def authentication_options(self, allowed: list[bytes]) -> tuple[dict[str, Any], bytes]:
        options = generate_authentication_options(
            rp_id=self.rp_id,
            allow_credentials=[PublicKeyCredentialDescriptor(id=c) for c in allowed],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return json.loads(options_to_json(options)), options.challenge

    def verify_authentication(
        self, credential: dict[str, Any], challenge: bytes, public_key: bytes, sign_count: int
    ) -> int:
        """Returns the new sign count."""
        try:
            result = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
                require_user_verification=True,
            )
        except InvalidAuthenticationResponse as exc:
            # py_webauthn reports a counter regression only through its message text.
            if "sign count" in str(exc).lower():
                raise SignCountRegression(str(exc)) from exc
            raise VerificationFailed(str(exc)) from exc
        except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
            raise VerificationFailed(str(exc)) from exc
        return result.new_sign_count


@dataclass
class _Pending:
    purpose: str
    challenge: bytes
    expires_at: datetime
    data: dict[str, Any] = field(default_factory=dict)


class ChallengeStore:
    """Outstanding challenges keyed by a random id (sent as an HttpOnly cookie).

    ponytail: in-process dict, single process only; move to a table if multi-process.
    """

    def __init__(self, clock: Clock, ttl: timedelta = timedelta(minutes=2)) -> None:
        self._clock = clock
        self._ttl = ttl
        self._pending: dict[str, _Pending] = {}

    def put(self, purpose: str, challenge: bytes, **data: Any) -> str:
        now = self._clock.now()
        self._pending = {k: v for k, v in self._pending.items() if v.expires_at > now}
        challenge_id = secrets.token_urlsafe(24)
        self._pending[challenge_id] = _Pending(purpose, challenge, now + self._ttl, data)
        return challenge_id

    def pop(self, challenge_id: str | None, purpose: str) -> tuple[bytes, dict[str, Any]] | None:
        """Single use: the challenge is removed whether or not it matches."""
        pending = self._pending.pop(challenge_id or "", None)
        if pending is None or pending.purpose != purpose:
            return None
        if pending.expires_at <= self._clock.now():
            return None
        return pending.challenge, pending.data
