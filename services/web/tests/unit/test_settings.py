import pytest
from web import settings as settings_module
from web.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # Prevent load_settings() from reading a real .env file during tests.
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("WEB_AUTH_API_URL", "http://localhost:8001")
    monkeypatch.setenv("WEB_INTERNAL_SECRET", "s3cret")


def test_loads_auth_api_url_from_env():
    result = load_settings()

    assert result.auth_api_url == "http://localhost:8001"


def test_trailing_slash_is_stripped_from_auth_api_url(monkeypatch):
    monkeypatch.setenv("WEB_AUTH_API_URL", "http://localhost:8001/")

    assert load_settings().auth_api_url == "http://localhost:8001"


def test_missing_auth_api_url_raises(monkeypatch):
    monkeypatch.delenv("WEB_AUTH_API_URL", raising=False)

    with pytest.raises(SettingsError, match="WEB_AUTH_API_URL"):
        load_settings()


def test_missing_internal_secret_raises(monkeypatch):
    monkeypatch.delenv("WEB_INTERNAL_SECRET", raising=False)

    with pytest.raises(SettingsError, match="WEB_INTERNAL_SECRET"):
        load_settings()


def test_defaults_are_applied():
    result = load_settings()

    assert result.cookie_secure is True
    assert result.api_host == "0.0.0.0"
    assert result.api_port == 8000
    assert result.http_timeout_seconds == 10.0
    assert result.brand_dir.name == "brand"


def test_overrides_are_read_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_COOKIE_SECURE", "false")
    monkeypatch.setenv("WEB_API_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_API_PORT", "9000")
    monkeypatch.setenv("WEB_HTTP_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("WEB_BRAND_DIR", str(tmp_path))

    result = load_settings()

    assert result.cookie_secure is False
    assert result.api_host == "127.0.0.1"
    assert result.api_port == 9000
    assert result.http_timeout_seconds == 5.0
    assert result.brand_dir == tmp_path


def test_empty_cookie_secure_falls_back_to_the_default(monkeypatch):
    # An env var present but empty (WEB_COOKIE_SECURE= in a .env, or an unset compose variable
    # interpolating to nothing) must not be read as "false" -- that would silently drop the
    # Secure flag from the CSRF and session cookies in production.
    monkeypatch.setenv("WEB_COOKIE_SECURE", "")

    assert load_settings().cookie_secure is True


def test_whitespace_only_cookie_secure_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("WEB_COOKIE_SECURE", "   ")

    assert load_settings().cookie_secure is True


def test_explicit_false_still_disables_cookie_secure(monkeypatch):
    monkeypatch.setenv("WEB_COOKIE_SECURE", "false")

    assert load_settings().cookie_secure is False
