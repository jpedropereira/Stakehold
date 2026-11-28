import pytest
from portfolio import settings as settings_module
from portfolio.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # Prevent load_settings() from reading a real .env file during tests.
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def admin_database_url(monkeypatch):
    # Set for every test by default; tests targeting this var override/delete it themselves.
    monkeypatch.setenv("APP_POSTGRES_ADMIN_DATABASE_URL", "postgresql://admin:pass@localhost/app")


def test_loads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")

    result = load_settings()

    assert result.database_url == "postgresql://user:pass@localhost/app"


def test_loads_admin_database_url_from_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.setenv("APP_POSTGRES_ADMIN_DATABASE_URL", "postgresql://admin:pass@localhost/app")

    result = load_settings()

    assert result.admin_database_url == "postgresql://admin:pass@localhost/app"


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="PORTFOLIO_DATABASE_URL"):
        load_settings()


def test_missing_admin_database_url_raises(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.delenv("APP_POSTGRES_ADMIN_DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="APP_POSTGRES_ADMIN_DATABASE_URL"):
        load_settings()


def test_data_stores_database_url_is_not_used(monkeypatch):
    """The two services share a pytest process; portfolio must not fall back to DATABASE_URL."""
    monkeypatch.delenv("PORTFOLIO_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://data_store:pass@localhost/data_store")

    with pytest.raises(SettingsError):
        load_settings()


def test_settings_no_longer_carry_a_ticker_mappings_path(monkeypatch):
    # Mappings live in the ticker_mappings table now; a leftover env var must not resurrect a
    # file that nothing reads.
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.setenv("PORTFOLIO_TICKER_MAPPINGS_PATH", "configs/ticker_mappings.yaml")

    result = load_settings()

    assert not hasattr(result, "ticker_mappings_path")


def test_datastore_api_url_and_internal_api_secret_default_to_none(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.delenv("DATASTORE_API_URL", raising=False)
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)

    result = load_settings()

    assert result.datastore_api_url is None
    assert result.internal_api_secret is None


def test_datastore_api_url_and_internal_api_secret_load_from_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.setenv("DATASTORE_API_URL", "http://localhost:8082")
    monkeypatch.setenv("INTERNAL_API_SECRET", "shared-secret")

    result = load_settings()

    assert result.datastore_api_url == "http://localhost:8082"
    assert result.internal_api_secret == "shared-secret"  # pragma: allowlist secret


def test_api_internal_secret_defaults_to_none(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.delenv("PORTFOLIO_INTERNAL_SECRET", raising=False)

    result = load_settings()

    assert result.api_internal_secret is None


def test_api_internal_secret_loads_from_its_own_prefixed_var(monkeypatch):
    """Deliberately a separate var from the unprefixed INTERNAL_API_SECRET (portfolio's outbound
    credential to data-store) -- this one is inbound, matching auth's AUTH_INTERNAL_SECRET
    convention, and must rotate independently of the outbound one."""
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.setenv("PORTFOLIO_INTERNAL_SECRET", "inbound-secret")
    monkeypatch.setenv("INTERNAL_API_SECRET", "outbound-secret")

    result = load_settings()

    assert result.api_internal_secret == "inbound-secret"  # pragma: allowlist secret
    assert result.internal_api_secret == "outbound-secret"  # pragma: allowlist secret


def test_api_host_and_port_default(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.delenv("PORTFOLIO_API_HOST", raising=False)
    monkeypatch.delenv("PORTFOLIO_API_PORT", raising=False)

    result = load_settings()

    assert result.api_host == "0.0.0.0"
    assert result.api_port == 8002


def test_api_host_and_port_load_from_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.setenv("PORTFOLIO_API_HOST", "127.0.0.1")
    monkeypatch.setenv("PORTFOLIO_API_PORT", "9001")

    result = load_settings()

    assert result.api_host == "127.0.0.1"
    assert result.api_port == 9001


def test_cross_user_database_url_defaults_to_none(monkeypatch):
    # Optional: only the cross-user jobs need it, and they check it themselves.
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.delenv("PORTFOLIO_CROSS_USER_DATABASE_URL", raising=False)

    assert load_settings().cross_user_database_url is None


def test_cross_user_database_url_loads_from_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")
    monkeypatch.setenv(
        "PORTFOLIO_CROSS_USER_DATABASE_URL", "postgresql://portfolio_cross_user:pass@localhost/app"
    )

    assert (
        load_settings().cross_user_database_url
        == "postgresql://portfolio_cross_user:pass@localhost/app"
    )
