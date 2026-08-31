from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Argon2id is argon2-cffi's default Type, matching architecture.md's choice over bcrypt.
_hasher = PasswordHasher()

# Generated from the real hasher rather than hand-written, so it's guaranteed to be a valid PHC
# string and stays in sync if the hasher's cost parameters ever change.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-parity")

# Bounds the input to argon2's hashing itself, not just request-body validation (schemas.py) --
# an attacker who could reach hash_password with an unbounded string could use it as a CPU
# exhaustion vector regardless of what the API layer otherwise rejects.
MAX_PASSWORD_LENGTH = 1024


class PasswordTooLong(Exception):
    pass


def hash_password(password: str) -> str:
    """A PHC string. TEXT column, not fixed-width -- see schema.md on why."""
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordTooLong(len(password))
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """False for any hash that doesn't verify, including one that can't be parsed at all.

    A truncated or otherwise corrupt `password_hash` column raises InvalidHashError, which is a
    ValueError and *not* a VerificationError. Letting it escape would turn a bad row into a 500
    and, worse, skip the caller's throttle bookkeeping -- so the unauthenticated caller sees a
    different response for that account and gets unlimited attempts at it.
    """
    try:
        _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """True once the hasher's cost parameters have moved on from what produced this hash."""
    return _hasher.check_needs_rehash(password_hash)


def dummy_verify() -> None:
    """Spends the same time verify_password would, for an email with no matching user.

    Without this, a login attempt against a nonexistent email returns near-instantly while one
    against a real email pays the Argon2 cost -- a timing side-channel that reveals which emails
    are registered.
    """
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-real-password")
    except VerificationError, InvalidHashError:
        pass
