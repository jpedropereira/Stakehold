import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    config_path: Path
    tracked_tickers_path: Path


def load_settings() -> Settings:
    load_dotenv()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SettingsError("DATABASE_URL is not set")

    config_path = Path(os.environ.get("DATA_STORE_CONFIG_PATH", "config/etfs.yaml"))
    tracked_tickers_path = Path(
        os.environ.get("DATA_STORE_TRACKED_TICKERS_PATH", "config/tracked_tickers.yaml")
    )

    return Settings(
        database_url=database_url,
        config_path=config_path,
        tracked_tickers_path=tracked_tickers_path,
    )
