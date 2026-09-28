"""Software passkey (ES256, attestation "none") that answers WebAuthn options like a browser."""

import hashlib
import json
import secrets
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

UP, UV, AT = 0x01, 0x04, 0x40


class SoftAuthenticator:
    def __init__(self, rp_id: str, origin: str) -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.sign_count = 0

    def _client_data(self, kind: str, options: dict[str, Any]) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": options["challenge"],
                "origin": self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def _auth_data(self, flags: int) -> bytes:
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_hash + bytes([flags]) + struct.pack(">I", self.sign_count)

    def create(self, options: dict[str, Any]) -> dict[str, Any]:
        """Answer PublicKeyCredentialCreationOptions (navigator.credentials.create)."""
        numbers = self.key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        attested = (
            bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose_key
        )
        attestation = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": self._auth_data(UP | UV | AT) + attested}
        )
        return self._credential(
            {
                "clientDataJSON": bytes_to_base64url(self._client_data("webauthn.create", options)),
                "attestationObject": bytes_to_base64url(attestation),
            }
        )

    def get(self, options: dict[str, Any]) -> dict[str, Any]:
        """Answer PublicKeyCredentialRequestOptions (navigator.credentials.get)."""
        self.sign_count += 1
        auth_data = self._auth_data(UP | UV)
        client_data = self._client_data("webauthn.get", options)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return self._credential(
            {
                "authenticatorData": bytes_to_base64url(auth_data),
                "clientDataJSON": bytes_to_base64url(client_data),
                "signature": bytes_to_base64url(signature),
            }
        )

    def _credential(self, response: dict[str, str]) -> dict[str, Any]:
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }
