import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Config paths are resolved against the service directory, not the process's working
# directory. Without this the service silently depends on where it was invoked from: the
# Airflow DAGs used to prefix `cd /opt/stakehold/services/data-store &&` to a shell command,
# which made the relative defaults work by accident and broke the moment a job was called
# directly rather than through a shell.
_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve(raw_path: str) -> Path:
    """An absolute override is honoured as given; a relative one is service-root-relative."""
    path = Path(raw_path)
    return path if path.is_absolute() else _SERVICE_ROOT / path


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    config_path: Path
    tracked_tickers_path: Path
    exchange_suffixes_path: Path
    regimes_csv_path: Path
    currencies_path: Path
    macro_regions_path: Path
    # Optional: every CLI/Airflow caller has no need of it, so making it required here would
    # break them. The read API checks it is set at its own startup instead -- see
    # data_store.api.app.
    internal_api_secret: str | None = None
    # Optional at this level for the same reason: only ingest-macro-indicators needs it.
    # run_ingest_macro_indicators raises SettingsError itself if it's unset -- see jobs.py.
    fred_api_key: str | None = None
    # How many new tickers one user may add per rolling 24 hours (admins aren't limited). Only
    # the read API's add-ticker route uses it.
    ticker_requests_per_day: int = 10


def load_settings() -> Settings:
    load_dotenv()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SettingsError("DATABASE_URL is not set")

    config_path = _resolve(os.environ.get("DATA_STORE_CONFIG_PATH", "config/etfs.yaml"))
    tracked_tickers_path = _resolve(
        os.environ.get("DATA_STORE_TRACKED_TICKERS_PATH", "config/tracked_tickers.yaml")
    )
    exchange_suffixes_path = _resolve(
        os.environ.get(
            "DATA_STORE_EXCHANGE_SUFFIXES_PATH", "config/yfinance_exchange_suffixes.yaml"
        )
    )
    regimes_csv_path = _resolve(os.environ.get("DATA_STORE_REGIMES_PATH", "config/regimes.csv"))
    currencies_path = _resolve(
        os.environ.get("DATA_STORE_CURRENCIES_PATH", "config/currencies.yaml")
    )
    macro_regions_path = _resolve(
        os.environ.get("DATA_STORE_MACRO_REGIONS_PATH", "config/macro_regions.yaml")
    )

    raw_limit = os.environ.get("DATA_STORE_TICKER_REQUESTS_PER_DAY", "10")
    if not raw_limit.isdigit():
        raise SettingsError(
            f"DATA_STORE_TICKER_REQUESTS_PER_DAY must be a non-negative integer, got {raw_limit!r}"
        )

    return Settings(
        database_url=database_url,
        config_path=config_path,
        tracked_tickers_path=tracked_tickers_path,
        exchange_suffixes_path=exchange_suffixes_path,
        regimes_csv_path=regimes_csv_path,
        currencies_path=currencies_path,
        macro_regions_path=macro_regions_path,
        internal_api_secret=os.environ.get("INTERNAL_API_SECRET") or None,
        fred_api_key=os.environ.get("FRED_API_KEY") or None,
        ticker_requests_per_day=int(raw_limit),
    )
