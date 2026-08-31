import os
from dataclasses import dataclass

from dotenv import load_dotenv


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_database_url: str


def load_settings() -> Settings:
    load_dotenv()

    # Deliberately not DATABASE_URL: data-store owns that name, and both services'
    # integration suites can run in one pytest process where a shared name would collide.
    # See docs/architecture.md's configuration section.
    database_url = os.environ.get("PORTFOLIO_DATABASE_URL")
    if not database_url:
        raise SettingsError("PORTFOLIO_DATABASE_URL is not set")

    # Instance-scoped, not service-scoped: this is the app-postgres bootstrap superuser,
    # shared by whatever service happens to run `db init` first. `jobs.py` uses it for exactly
    # one thing -- creating this service's own non-superuser role and schema -- and never wires
    # it into anything the running app queries with. See docs/architecture.md's deployment
    # section on why the app's own connection must never hold this role.
    admin_database_url = os.environ.get("APP_POSTGRES_ADMIN_DATABASE_URL")
    if not admin_database_url:
        raise SettingsError("APP_POSTGRES_ADMIN_DATABASE_URL is not set")

    return Settings(database_url=database_url, admin_database_url=admin_database_url)
