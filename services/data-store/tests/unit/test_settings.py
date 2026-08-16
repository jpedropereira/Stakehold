from pathlib import Path

import pytest
from data_store import settings as settings_module
from data_store.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    # Prevent load_settings() from reading a real .env file during tests.
    monkeypatch.setattr(settings_module, "load_dotenv", lambda *args, **kwargs: None)


def test_loads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("DATA_STORE_CONFIG_PATH", raising=False)

    result = load_settings()

    assert result.database_url == "postgresql://user:pass@localhost/db"


def test_config_path_defaults_to_config_etfs_yaml(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("DATA_STORE_CONFIG_PATH", raising=False)

    result = load_settings()

    assert result.config_path == Path("config/etfs.yaml")


def test_config_path_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_CONFIG_PATH", str(tmp_path / "custom.yaml"))

    result = load_settings()

    assert result.config_path == tmp_path / "custom.yaml"


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="DATABASE_URL"):
        load_settings()
