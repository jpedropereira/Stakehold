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

    assert (
        result.config_path
        == Path(settings_module.__file__).resolve().parent.parent / "config/etfs.yaml"
    )


def test_config_path_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_CONFIG_PATH", str(tmp_path / "custom.yaml"))

    result = load_settings()

    assert result.config_path == tmp_path / "custom.yaml"


def test_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="DATABASE_URL"):
        load_settings()


def test_tracked_tickers_path_defaults_to_config_tracked_tickers_yaml(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("DATA_STORE_TRACKED_TICKERS_PATH", raising=False)

    result = load_settings()

    assert (
        result.tracked_tickers_path
        == Path(settings_module.__file__).resolve().parent.parent / "config/tracked_tickers.yaml"
    )


def test_tracked_tickers_path_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_TRACKED_TICKERS_PATH", str(tmp_path / "custom.yaml"))

    result = load_settings()

    assert result.tracked_tickers_path == tmp_path / "custom.yaml"


def test_exchange_suffixes_path_defaults_to_config_yfinance_exchange_suffixes_yaml(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("DATA_STORE_EXCHANGE_SUFFIXES_PATH", raising=False)

    result = load_settings()

    assert (
        result.exchange_suffixes_path
        == Path(settings_module.__file__).resolve().parent.parent
        / "config/yfinance_exchange_suffixes.yaml"
    )


def test_exchange_suffixes_path_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_EXCHANGE_SUFFIXES_PATH", str(tmp_path / "custom.yaml"))

    result = load_settings()

    assert result.exchange_suffixes_path == tmp_path / "custom.yaml"


def test_relative_config_paths_resolve_against_the_service_root_not_the_cwd(monkeypatch, tmp_path):
    # The service must not care where it was invoked from. It used to: the default paths were
    # relative, so they only resolved when the process happened to be running in
    # services/data-store. The Airflow DAGs got away with it by prefixing `cd /opt/stakehold/...`
    # to a shell command -- an invisible dependency that broke the moment the job was called
    # directly instead of through a shell.
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    for var in (
        "DATA_STORE_CONFIG_PATH",
        "DATA_STORE_TRACKED_TICKERS_PATH",
        "DATA_STORE_EXCHANGE_SUFFIXES_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    result = load_settings()

    service_root = Path(settings_module.__file__).resolve().parent.parent
    assert result.config_path == service_root / "config/etfs.yaml"
    assert result.tracked_tickers_path == service_root / "config/tracked_tickers.yaml"
    assert result.exchange_suffixes_path == service_root / "config/yfinance_exchange_suffixes.yaml"
    assert result.config_path.is_absolute()


def test_relative_override_paths_also_resolve_against_the_service_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_CONFIG_PATH", "somewhere/funds.yaml")
    monkeypatch.chdir(tmp_path)

    result = load_settings()

    service_root = Path(settings_module.__file__).resolve().parent.parent
    assert result.config_path == service_root / "somewhere/funds.yaml"


def test_absolute_override_paths_are_left_alone(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_CONFIG_PATH", "/etc/stakehold/funds.yaml")

    assert load_settings().config_path == Path("/etc/stakehold/funds.yaml")
