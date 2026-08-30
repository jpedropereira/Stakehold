import pytest
from portfolio import settings as settings_module
from portfolio.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # Prevent load_settings() from reading a real .env file during tests.
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *args, **kwargs: None)


def test_loads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", "postgresql://user:pass@localhost/app")

    result = load_settings()

    assert result.database_url == "postgresql://user:pass@localhost/app"


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="PORTFOLIO_DATABASE_URL"):
        load_settings()


def test_data_stores_database_url_is_not_used(monkeypatch):
    """The two services share a pytest process; portfolio must not fall back to DATABASE_URL."""
    monkeypatch.delenv("PORTFOLIO_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://data_store:pass@localhost/data_store")

    with pytest.raises(SettingsError):
        load_settings()
