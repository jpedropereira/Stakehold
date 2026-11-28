import os
from dataclasses import dataclass

from dotenv import load_dotenv
from shared.env import int_env


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_database_url: str
    # Both optional: only import-cashflow needs a live data-store call, so requiring these for
    # every command (account add, add-transaction, import-transactions) would break them for no
    # reason. run_import_cashflow checks both are set itself, the same way data-store's own API
    # checks internal_api_secret at its own startup rather than here.
    datastore_api_url: str | None
    internal_api_secret: str | None
    # The HTTP API's own inbound credential -- deliberately a separate, prefixed var from the
    # unprefixed internal_api_secret above (which is portfolio's *outbound* credential to
    # data-store). Keeping them distinct lets each side rotate independently; sharing one secret
    # both ways would mean a leak on either edge compromises both. Optional here for the same
    # reason internal_api_secret is: the CLI must keep working with no API ever run, and
    # api/app.py checks this itself at its own startup, data-store's own pattern.
    api_internal_secret: str | None
    api_host: str
    api_port: int
    # The read-only role that bypasses row-level security, for the few reads that enumerate
    # across users (docs/architecture.md's "User isolation"). Optional for the same reason
    # datastore_api_url is: most commands act for one user and never need it, so
    # db/connection.cross_user_connection checks it itself.
    cross_user_database_url: str | None = None


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

    return Settings(
        database_url=database_url,
        admin_database_url=admin_database_url,
        datastore_api_url=os.environ.get("DATASTORE_API_URL") or None,
        # Deliberately not PORTFOLIO_-prefixed: this is the one shared credential both portfolio
        # and data-store must agree on, matching data-store's own env var name exactly.
        internal_api_secret=os.environ.get("INTERNAL_API_SECRET") or None,
        api_internal_secret=os.environ.get("PORTFOLIO_INTERNAL_SECRET") or None,
        api_host=os.environ.get("PORTFOLIO_API_HOST", "0.0.0.0"),
        # 8002: web is 8000, auth is 8001, data-store's read API is 8082.
        api_port=int_env("PORTFOLIO_API_PORT", 8002),
        cross_user_database_url=os.environ.get("PORTFOLIO_CROSS_USER_DATABASE_URL") or None,
    )
