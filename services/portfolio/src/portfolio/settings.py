import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Config paths are resolved against the service directory, not the process's working
# directory -- same reasoning and mechanism as data-store/settings.py's _SERVICE_ROOT/_resolve.
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
    admin_database_url: str
    ticker_mappings_path: Path
    # Both optional: only import-acme-cashflows needs a live data-store call, so requiring
    # these for every command (account add, add-transaction, import-transactions) would break
    # them for no reason. run_import_acme_cashflows checks both are set itself, the same way
    # data-store's own API checks internal_api_secret at its own startup rather than here.
    datastore_api_url: str | None
    internal_api_secret: str | None


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

    ticker_mappings_path = _resolve(
        os.environ.get("PORTFOLIO_TICKER_MAPPINGS_PATH", "configs/ticker_mappings.yaml")
    )

    return Settings(
        database_url=database_url,
        admin_database_url=admin_database_url,
        ticker_mappings_path=ticker_mappings_path,
        datastore_api_url=os.environ.get("DATASTORE_API_URL") or None,
        # Deliberately not PORTFOLIO_-prefixed: this is the one shared credential both portfolio
        # and data-store must agree on, matching data-store's own env var name exactly.
        internal_api_secret=os.environ.get("INTERNAL_API_SECRET") or None,
    )
