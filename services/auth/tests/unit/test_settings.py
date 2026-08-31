import pytest
from auth import settings as settings_module
from auth.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # Prevent load_settings() from reading a real .env file during tests.
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("AUTH_INTERNAL_SECRET", "s3cret")


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
