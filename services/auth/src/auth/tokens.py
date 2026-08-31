import hashlib
import secrets

# 32 random bytes, base64url-encoded -- matches schema.md's description of the cookie value.
_TOKEN_BYTES = 32


def generate_token() -> str:
    """A new opaque session token. Never stored -- only its hash (hash_token) is."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    """SHA-256 of the raw token, for the `sessions.token_hash` BYTEA column.

    Storing only the hash means reading the table (a DB dump, a misdirected query, a backup)
    can't be replayed as a live session -- see schema.md.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()
