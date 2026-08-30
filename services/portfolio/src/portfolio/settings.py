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

    # Deliberately not DATABASE_URL: data-store owns that name, and both services'
    # integration suites can run in one pytest process where a shared name would collide.
    # See docs/architecture.md's configuration section.
    database_url = os.environ.get("PORTFOLIO_DATABASE_URL")
    if not database_url:
        raise SettingsError("PORTFOLIO_DATABASE_URL is not set")

    return Settings(database_url=database_url)
