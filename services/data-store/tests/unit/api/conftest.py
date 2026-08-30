import pytest
from data_store.api.routes import health_router, router
from data_store.api.security import require_internal_secret
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """A bare FastAPI app wired with the real routes but with auth bypassed -- auth itself is
    covered by test_security.py, so route tests stub only `data_store.jobs`, per-test."""
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(router)
    app.dependency_overrides[require_internal_secret] = lambda: None
    return TestClient(app)
