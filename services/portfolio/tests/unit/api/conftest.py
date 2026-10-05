import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from portfolio.api.routes import health_router, router
from portfolio.api.security import require_internal_secret
from portfolio.settings import Settings


@pytest.fixture
def client():
    """A bare FastAPI app wired with the real routes but with auth bypassed -- auth itself is
    covered by test_security.py, so route tests stub only `portfolio.jobs`, per-test. Mirrors
    data-store's own tests/unit/api/conftest.py.

    `app.state.settings` is still set, even with auth bypassed -- every route now depends on
    `get_settings` to pass its own `Settings` into `jobs.*` (see jobs.py's module docstring), and
    a route test stubs `jobs.*` outright so the placeholder value here is never read for real."""
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(router)
    app.dependency_overrides[require_internal_secret] = lambda: None
    app.state.settings = Settings(
        database_url="postgresql://unused/unused",  # pragma: allowlist secret
        admin_database_url="postgresql://unused/unused",  # pragma: allowlist secret
        datastore_api_url=None,
        internal_api_secret=None,
        api_internal_secret="unused",  # pragma: allowlist secret
        api_host="0.0.0.0",  # noqa
        api_port=8002,
    )
    return TestClient(app)
