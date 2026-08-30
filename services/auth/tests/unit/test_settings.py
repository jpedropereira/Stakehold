import pytest
from auth import settings as settings_module
from auth.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # Prevent load_settings() from reading a real .env file during tests.
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *args, **kwargs: None)


def test_loads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://user:pass@localhost/db")

    result = load_settings()

    assert result.database_url == "postgresql://user:pass@localhost/db"


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("AUTH_DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="AUTH_DATABASE_URL"):
        load_settings()
