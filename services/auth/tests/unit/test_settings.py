import pytest
from auth import settings as settings_module
from auth.settings import SettingsError, load_settings
from auth.signing import load_signing_key
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa


def _pem(private_key) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _ed25519_pem() -> bytes:
    return _pem(ed25519.Ed25519PrivateKey.generate())


# Generated per run, never committed.
SIGNING_KEY_PEM = _ed25519_pem()


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # Prevent load_settings() from reading a real .env file during tests.
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("AUTH_INTERNAL_SECRET", "s3cret")
    monkeypatch.setenv("AUTH_SIGNING_KEY", SIGNING_KEY_PEM.decode("ascii"))
    # A developer's shell may export the file variant; it must not leak into these tests.
    monkeypatch.delenv("AUTH_SIGNING_KEY_FILE", raising=False)
    monkeypatch.delenv("AUTH_DELEGATED_TOKEN_TTL_SECONDS", raising=False)


def test_loads_database_url_from_env():
    result = load_settings()

    assert result.database_url == "postgresql://user:pass@localhost/db"


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("AUTH_DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="AUTH_DATABASE_URL"):
        load_settings()


def test_missing_internal_secret_raises(monkeypatch):
    monkeypatch.delenv("AUTH_INTERNAL_SECRET", raising=False)

    with pytest.raises(SettingsError, match="AUTH_INTERNAL_SECRET"):
        load_settings()


def test_defaults_are_applied():
    result = load_settings()

    assert result.session_ttl_days == 14
    assert result.session_absolute_ttl_days == 90
    assert result.cookie_secure is True
    assert result.api_host == "0.0.0.0"
    assert result.api_port == 8001
    assert result.pool_min_size == 1
    assert result.pool_max_size == 10


def test_overrides_are_read_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_SESSION_TTL_DAYS", "7")
    monkeypatch.setenv("AUTH_SESSION_ABSOLUTE_TTL_DAYS", "30")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_API_HOST", "127.0.0.1")
    monkeypatch.setenv("AUTH_API_PORT", "9001")
    monkeypatch.setenv("AUTH_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("AUTH_POOL_MAX_SIZE", "20")

    result = load_settings()

    assert result.session_ttl_days == 7
    assert result.session_absolute_ttl_days == 30
    assert result.cookie_secure is False
    assert result.api_host == "127.0.0.1"
    assert result.api_port == 9001
    assert result.pool_min_size == 2
    assert result.pool_max_size == 20


def test_empty_cookie_secure_falls_back_to_the_default(monkeypatch):
    # An env var present but empty (AUTH_COOKIE_SECURE= in a .env, or an unset compose variable
    # interpolating to nothing) must not be read as "false" -- that would silently drop the
    # session cookie's Secure flag in production. Matches how _int_env treats an empty value.
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "")

    assert load_settings().cookie_secure is True


def test_whitespace_only_cookie_secure_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "   ")

    assert load_settings().cookie_secure is True


def test_empty_int_override_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("AUTH_SESSION_TTL_DAYS", "")

    assert load_settings().session_ttl_days == 14


def test_explicit_false_still_disables_cookie_secure(monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    assert load_settings().cookie_secure is False


# --- signing key ------------------------------------------------------------


def test_loads_the_signing_key_from_an_inline_pem():
    result = load_settings()

    assert result.signing_key.kid == load_signing_key(SIGNING_KEY_PEM).kid


def test_loads_the_signing_key_from_a_file(monkeypatch, tmp_path):
    pem = _ed25519_pem()
    key_file = tmp_path / "signing-key.pem"
    key_file.write_bytes(pem)
    monkeypatch.delenv("AUTH_SIGNING_KEY")
    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(key_file))

    result = load_settings()

    assert result.signing_key.kid == load_signing_key(pem).kid


def test_missing_signing_key_raises(monkeypatch):
    # Required, not optional: a deploy without a key fails at startup rather than on the first
    # chat message.
    monkeypatch.delenv("AUTH_SIGNING_KEY")

    with pytest.raises(SettingsError, match="AUTH_SIGNING_KEY"):
        load_settings()


def test_setting_both_key_sources_raises(monkeypatch, tmp_path):
    # Two sources that could disagree, with no way to tell which one a running auth used.
    key_file = tmp_path / "signing-key.pem"
    key_file.write_bytes(_ed25519_pem())
    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(key_file))

    with pytest.raises(SettingsError, match="both"):
        load_settings()


def test_unreadable_signing_key_file_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTH_SIGNING_KEY")
    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(tmp_path / "missing.pem"))

    with pytest.raises(SettingsError, match="AUTH_SIGNING_KEY_FILE"):
        load_settings()


def test_non_ed25519_signing_key_raises(monkeypatch):
    rsa_pem = _pem(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    monkeypatch.setenv("AUTH_SIGNING_KEY", rsa_pem.decode("ascii"))

    with pytest.raises(SettingsError, match="Ed25519"):
        load_settings()


def test_malformed_signing_key_error_does_not_echo_the_value(monkeypatch):
    monkeypatch.setenv("AUTH_SIGNING_KEY", "not-a-key-but-could-be-a-secret")

    with pytest.raises(SettingsError) as raised:
        load_settings()

    assert "not-a-key-but-could-be-a-secret" not in str(raised.value)


def test_settings_repr_does_not_reveal_the_signing_key():
    result = load_settings()

    assert "PRIVATE KEY" not in repr(result)
    assert result.signing_key.kid in repr(result)


# --- delegated token TTL ----------------------------------------------------


def test_delegated_token_ttl_defaults_to_five_minutes():
    assert load_settings().delegated_token_ttl_seconds == 300


def test_delegated_token_ttl_is_read_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_DELEGATED_TOKEN_TTL_SECONDS", "120")

    assert load_settings().delegated_token_ttl_seconds == 120


@pytest.mark.parametrize("value", ["0", "-5"])
def test_non_positive_delegated_token_ttl_raises(monkeypatch, value):
    # A token that expires as it is issued would make every chat request fail verification,
    # far from the misconfiguration that caused it.
    monkeypatch.setenv("AUTH_DELEGATED_TOKEN_TTL_SECONDS", value)

    with pytest.raises(SettingsError, match="AUTH_DELEGATED_TOKEN_TTL_SECONDS"):
        load_settings()
