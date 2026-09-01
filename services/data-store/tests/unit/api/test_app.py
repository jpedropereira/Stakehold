import pytest
from data_store.api import app as app_module
from data_store.api.app import create_app
from data_store.settings import Settings, SettingsError
from fastapi.testclient import TestClient


def settings_without_a_secret():
    return Settings(
        database_url="postgresql://user:pass@localhost/db",  # pragma: allowlist secret
        config_path=None,
        tracked_tickers_path=None,
        exchange_suffixes_path=None,
        regimes_csv_path=None,
        currencies_path=None,
        macro_regions_path=None,
        internal_api_secret=None,
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("INTERNAL_API_SECRET", "s3cret")
    return TestClient(create_app())


def test_health_is_reachable_without_credentials(client):
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_schema_and_docs_endpoints_are_not_served_unauthenticated(client, path):
    """They used to be: the bearer dependency hangs off `router`, and FastAPI mounts these as
    plain Starlette routes, so they answered 200 to anyone -- handing an unauthenticated caller
    the whole route/parameter surface."""
    assert client.get(path).status_code == 404


def test_data_routes_still_require_the_secret(client):
    assert client.get("/tickers/AAPL").status_code == 401


def test_refuses_to_start_without_a_secret(monkeypatch):
    # Patched rather than driven by the environment: `load_settings` calls `load_dotenv()`, so
    # unsetting the variable here would still pick up a developer's own .env.
    monkeypatch.setattr(app_module, "load_settings", lambda: settings_without_a_secret())

    with pytest.raises(SettingsError):
        create_app()
