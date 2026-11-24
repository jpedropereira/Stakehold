"""Minting the delegated token: the claims PD-8's verifier relies on, the expiry, and the signature.

Decoded with PyJWT against the public key -- the same check a verifier makes -- rather than by
inspecting the encoded string.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from auth.delegation import mint
from auth.signing import load_signing_key
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
USER_ID = uuid.UUID("01900000-0000-7000-8000-000000000001")
TTL = 300


def _key():
    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return load_signing_key(pem)


@pytest.fixture
def key():
    return _key()


def _decode(token, key, *, at=NOW, audience="portfolio"):
    # PyJWT checks exp against the wall clock; pin "now" to the test's clock instead, so the
    # expiry assertions don't depend on when the suite runs.
    leeway = (datetime.now(UTC) - at).total_seconds()
    return jwt.decode(
        token,
        key.private_key.public_key(),
        algorithms=["EdDSA"],
        audience=audience,
        issuer="auth",
        leeway=leeway,
    )


def test_token_carries_the_agreed_claims(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    claims = _decode(minted.token, key)

    assert claims["iss"] == "auth"
    assert claims["sub"] == str(USER_ID)
    assert claims["aud"] == ["agent", "portfolio"]
    assert claims["azp"] == "agent"
    assert claims["scope"] == "portfolio:read"
    assert claims["iat"] == int(NOW.timestamp())
    assert claims["exp"] == int(NOW.timestamp()) + TTL
    assert claims["jti"] == minted.jti
    assert set(claims) == {"iss", "sub", "aud", "azp", "scope", "iat", "exp", "jti"}


@pytest.mark.parametrize("audience", ["agent", "portfolio"])
def test_token_verifies_for_each_audience(key, audience):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    assert _decode(minted.token, key, audience=audience)["sub"] == str(USER_ID)


def test_token_is_rejected_for_another_audience(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    with pytest.raises(jwt.InvalidAudienceError):
        _decode(minted.token, key, audience="data-store")


def test_header_names_the_algorithm_and_the_key(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    header = jwt.get_unverified_header(minted.token)

    assert header["alg"] == "EdDSA"
    assert header["kid"] == key.kid
    assert header["typ"] == "JWT"


def test_expires_in_reports_the_ttl(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    assert minted.expires_in == TTL


def test_ttl_is_configurable(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=60)

    claims = _decode(minted.token, key)

    assert claims["exp"] - claims["iat"] == 60
    assert minted.expires_in == 60


def test_token_is_still_valid_just_before_expiry(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    claims = _decode(minted.token, key, at=NOW + timedelta(seconds=TTL - 1))

    assert claims["sub"] == str(USER_ID)


def test_token_is_rejected_after_expiry(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    with pytest.raises(jwt.ExpiredSignatureError):
        _decode(minted.token, key, at=NOW + timedelta(seconds=TTL + 1))


def test_each_token_gets_its_own_jti(key):
    first = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)
    second = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    assert first.jti != second.jti


def test_token_signed_by_one_key_does_not_verify_under_another(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    with pytest.raises(jwt.InvalidSignatureError):
        _decode(minted.token, _key())


def test_repr_does_not_reveal_the_token(key):
    minted = mint(key, user_id=USER_ID, now=NOW, ttl_seconds=TTL)

    assert minted.token not in repr(minted)
