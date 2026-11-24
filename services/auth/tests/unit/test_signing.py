"""The signing key auth mints delegated tokens with, and the JWKS it publishes for verifiers.

Keys are generated per test, never committed: a private key in the repo, even a test one, is
exactly what detect-secrets exists to stop.
"""

import base64
import hashlib
import json

import pytest
from auth.signing import SigningKeyError, load_signing_key, public_jwks
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa


def _pem(private_key) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _ed25519_pem() -> bytes:
    return _pem(ed25519.Ed25519PrivateKey.generate())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_loads_an_ed25519_pkcs8_pem():
    key = load_signing_key(_ed25519_pem())

    assert isinstance(key.private_key, ed25519.Ed25519PrivateKey)


def test_rejects_an_rsa_key():
    pem = _pem(rsa.generate_private_key(public_exponent=65537, key_size=2048))

    with pytest.raises(SigningKeyError, match="Ed25519"):
        load_signing_key(pem)


def test_rejects_an_ec_key():
    pem = _pem(ec.generate_private_key(ec.SECP256R1()))

    with pytest.raises(SigningKeyError, match="Ed25519"):
        load_signing_key(pem)


def test_rejects_bytes_that_are_not_a_pem_key():
    with pytest.raises(SigningKeyError):
        load_signing_key(b"not a key")


def test_rejects_a_public_key_pem():
    public_pem = (
        ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    with pytest.raises(SigningKeyError):
        load_signing_key(public_pem)


def test_kid_is_the_rfc_7638_thumbprint_of_the_public_key():
    key = load_signing_key(_ed25519_pem())
    raw_public = key.private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    # RFC 7638 §3.2: the required members only, lexicographic order, no whitespace.
    canonical = json.dumps(
        {"crv": "Ed25519", "kty": "OKP", "x": _b64url(raw_public)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert key.kid == _b64url(hashlib.sha256(canonical).digest())


def test_kid_is_stable_for_one_key():
    pem = _ed25519_pem()

    assert load_signing_key(pem).kid == load_signing_key(pem).kid


def test_kid_differs_between_keys():
    assert load_signing_key(_ed25519_pem()).kid != load_signing_key(_ed25519_pem()).kid


def test_jwks_publishes_exactly_one_public_key_with_its_kid():
    key = load_signing_key(_ed25519_pem())

    jwks = public_jwks(key)

    assert list(jwks) == ["keys"]
    assert len(jwks["keys"]) == 1
    jwk = jwks["keys"][0]
    assert jwk["kty"] == "OKP"
    assert jwk["crv"] == "Ed25519"
    assert jwk["alg"] == "EdDSA"
    assert jwk["use"] == "sig"
    assert jwk["kid"] == key.kid


def test_jwks_public_key_matches_the_signing_key():
    key = load_signing_key(_ed25519_pem())
    raw_public = key.private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )

    jwk = public_jwks(key)["keys"][0]

    assert jwk["x"] == _b64url(raw_public)


def test_jwks_never_carries_private_material():
    jwk = public_jwks(load_signing_key(_ed25519_pem()))["keys"][0]

    # `d` is the private scalar in an OKP JWK (RFC 8037 §2).
    assert "d" not in jwk
    assert set(jwk) == {"kty", "crv", "x", "kid", "alg", "use"}
