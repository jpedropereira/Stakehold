"""The Ed25519 key auth signs delegated tokens with, and the JWKS that publishes its public half.

Asymmetric, not HMAC: `agent` and `portfolio` verify delegated tokens, and with a shared HMAC key
every verifier could mint tokens too -- a compromised `agent` could then forge one for any user.
See architecture.md, "Delegated tokens".
"""

import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ALGORITHM = "EdDSA"


class SigningKeyError(Exception):
    """Raised when the configured signing key can't be used."""


@dataclass(frozen=True)
class SigningKey:
    private_key: Ed25519PrivateKey
    kid: str

    def __repr__(self) -> str:
        # The dataclass default would print the key object; keep even that out of any log or
        # traceback that formats Settings.
        return f"SigningKey(kid={self.kid!r})"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _public_x(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return _b64url(raw)


def _thumbprint(x: str) -> str:
    """RFC 7638 JWK thumbprint: SHA-256 over the required members in lexicographic order, with
    no whitespace. Computed rather than configured, so the kid can never drift from the key."""
    canonical = json.dumps(
        {"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return _b64url(hashlib.sha256(canonical).digest())


def load_signing_key(pem: bytes) -> SigningKey:
    try:
        private_key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
        # The underlying message can echo parts of the input; don't chain it into the error text.
        raise SigningKeyError("signing key is not an unencrypted PEM private key") from exc

    if not isinstance(private_key, Ed25519PrivateKey):
        raise SigningKeyError(f"signing key must be Ed25519, got {type(private_key).__name__}")

    return SigningKey(private_key=private_key, kid=_thumbprint(_public_x(private_key)))


def public_jwks(key: SigningKey) -> dict:
    """The JWK Set verifiers fetch: the public half only, never `d`."""
    return {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": _public_x(key.private_key),
                "kid": key.kid,
                "alg": ALGORITHM,
                "use": "sig",
            }
        ]
    }
