import os
from dataclasses import dataclass

from dotenv import load_dotenv


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    database_url: str


def load_settings() -> Settings:
    load_dotenv()

    database_url = os.environ.get("AUTH_DATABASE_URL")
    if not database_url:
        raise SettingsError("AUTH_DATABASE_URL is not set")

    return Settings(database_url=database_url)
