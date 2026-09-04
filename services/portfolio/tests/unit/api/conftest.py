import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from portfolio.api.routes import health_router, router
from portfolio.api.security import require_internal_secret


@pytest.fixture
def client():
    """A bare FastAPI app wired with the real routes but with auth bypassed -- auth itself is
    covered by test_security.py, so route tests stub only `portfolio.jobs`, per-test. Mirrors
    data-store's own tests/unit/api/conftest.py."""
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(router)
    app.dependency_overrides[require_internal_secret] = lambda: None
    return TestClient(app)
