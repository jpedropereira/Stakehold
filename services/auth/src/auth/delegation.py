"""Minting the short-lived, read-scoped token `web` hands to `agent` for one chat request.

Pure: the clock and the key are passed in. The claim set is agent/docs/architecture.md's
"Identity and authorization: the delegated token", plus `iss` and `iat`, which the shared
verifier (PD-8) checks.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import jwt
from auth.signing import ALGORITHM, SigningKey

# Constants, not configuration: every verifier must agree on them, so a per-deployment override
# could only ever break verification.
ISSUER = "auth"
AUDIENCE = ("agent", "portfolio")
AUTHORIZED_PARTY = "agent"
SCOPE = "portfolio:read"


@dataclass(frozen=True)
class DelegatedToken:
    # repr=False: the token is a bearer credential, so it must not reach a log line or traceback
    # through an accidental `%r`.
    token: str = field(repr=False)
    expires_in: int
    jti: str


def mint(key: SigningKey, *, user_id: uuid.UUID, now: datetime, ttl_seconds: int) -> DelegatedToken:
    issued_at = int(now.timestamp())
    jti = uuid.uuid4().hex
    claims = {
        "iss": ISSUER,
        "sub": str(user_id),
        "aud": list(AUDIENCE),
        "azp": AUTHORIZED_PARTY,
        "scope": SCOPE,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
        "jti": jti,
    }
    token = jwt.encode(
        claims, key.private_key, algorithm=ALGORITHM, headers={"kid": key.kid, "typ": "JWT"}
    )
    return DelegatedToken(token=token, expires_in=ttl_seconds, jti=jti)
