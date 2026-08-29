from pathlib import Path

import pytest
from data_store import settings as settings_module
from data_store.settings import SettingsError, load_settings

# Computed independently of settings.py's own _SERVICE_ROOT logic: this file lives at
# services/data-store/tests/unit/test_settings.py, three levels below services/data-store.
# Deriving the expectation the same way the implementation does would make these tests
# tautological -- they'd pass even if _SERVICE_ROOT pointed at the wrong directory, which is
# exactly what happened when the package moved under src/ and _SERVICE_ROOT wasn't updated to
# match.
_SERVICE_ROOT = Path(__file__).resolve().parents[2]


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

    assert result.config_path == _SERVICE_ROOT / "config/etfs.yaml"


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

    assert result.tracked_tickers_path == _SERVICE_ROOT / "config/tracked_tickers.yaml"


def test_tracked_tickers_path_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_TRACKED_TICKERS_PATH", str(tmp_path / "custom.yaml"))

    result = load_settings()

    assert result.tracked_tickers_path == tmp_path / "custom.yaml"


def test_exchange_suffixes_path_defaults_to_config_yfinance_exchange_suffixes_yaml(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("DATA_STORE_EXCHANGE_SUFFIXES_PATH", raising=False)

    result = load_settings()

    assert result.exchange_suffixes_path == _SERVICE_ROOT / "config/yfinance_exchange_suffixes.yaml"


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

    service_root = _SERVICE_ROOT
    assert result.config_path == service_root / "config/etfs.yaml"
    assert result.tracked_tickers_path == service_root / "config/tracked_tickers.yaml"
    assert result.exchange_suffixes_path == service_root / "config/yfinance_exchange_suffixes.yaml"
    assert result.config_path.is_absolute()


def test_relative_override_paths_also_resolve_against_the_service_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_CONFIG_PATH", "somewhere/funds.yaml")
    monkeypatch.chdir(tmp_path)

    result = load_settings()

    service_root = _SERVICE_ROOT
    assert result.config_path == service_root / "somewhere/funds.yaml"


def test_absolute_override_paths_are_left_alone(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_CONFIG_PATH", "/etc/stakehold/funds.yaml")

    assert load_settings().config_path == Path("/etc/stakehold/funds.yaml")


def test_regimes_csv_path_defaults_to_config_regimes_csv(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("DATA_STORE_REGIMES_PATH", raising=False)

    result = load_settings()

    assert result.regimes_csv_path == _SERVICE_ROOT / "config/regimes.csv"


def test_regimes_csv_path_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_REGIMES_PATH", str(tmp_path / "custom.csv"))

    result = load_settings()

    assert result.regimes_csv_path == tmp_path / "custom.csv"


def test_currencies_path_defaults_to_config_currencies_yaml(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("DATA_STORE_CURRENCIES_PATH", raising=False)

    result = load_settings()

    assert result.currencies_path == _SERVICE_ROOT / "config/currencies.yaml"


def test_currencies_path_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DATA_STORE_CURRENCIES_PATH", str(tmp_path / "custom.yaml"))

    result = load_settings()

    assert result.currencies_path == tmp_path / "custom.yaml"
