import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from portfolio import jobs
from portfolio.api.app import create_app
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    BrokerFileError,
    BrokerParserNotFound,
    BrokerPluginError,
    DataStoreApiError,
    InvalidBroker,
    InvalidTransaction,
    PortfolioError,
    TickerMappingError,
)
from portfolio.settings import Settings, SettingsError


def _settings(**overrides):
    values = dict(
        database_url="postgresql://portfolio:pw@localhost/app",  # pragma: allowlist secret
        admin_database_url="postgresql://admin:pw@localhost/app",  # pragma: allowlist secret
        ticker_mappings_path=Path("configs/ticker_mappings.yaml"),
        datastore_api_url=None,
        internal_api_secret=None,
        api_internal_secret="s3cret",  # pragma: allowlist secret
        api_host="0.0.0.0",  # noqa
        api_port=8002,
    )
    values.update(overrides)
    return Settings(**values)


def test_create_app_raises_without_an_internal_secret():
    with pytest.raises(SettingsError, match="PORTFOLIO_INTERNAL_SECRET"):
        create_app(_settings(api_internal_secret=None))


def test_docs_routes_are_disabled():
    app = create_app(_settings())
    client = TestClient(app)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_health_is_reachable_without_the_internal_secret(monkeypatch):
    app = create_app(_settings())
    client = TestClient(app)

    class _FakeCursor:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return ("t", "t", "t", "t")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr("portfolio.api.routes.get_connection", lambda url: _FakeConn())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "exc_type,status_code",
    [
        (AccountNotFound, 404),
        (AccountLabelConflict, 409),
        (InvalidBroker, 422),
        (InvalidTransaction, 422),
        (BrokerFileError, 422),
        (BrokerParserNotFound, 422),
        (TickerMappingError, 500),
        (BrokerPluginError, 500),
        (DataStoreApiError, 502),
    ],
)
def test_each_portfolio_error_subclass_maps_to_its_own_status(monkeypatch, exc_type, status_code):
    app = create_app(_settings())
    client = TestClient(app)

    def boom(**kw):
        raise exc_type("boom")

    monkeypatch.setattr(jobs, "run_list_accounts", boom)

    response = client.get(
        "/accounts",
        params={"user_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": "s3cret"},  # pragma: allowlist secret
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": "boom"}


def test_an_unmapped_portfolio_error_falls_back_to_500(monkeypatch):
    class SomeOtherDomainError(PortfolioError):
        pass

    app = create_app(_settings())
    client = TestClient(app)

    def boom(**kw):
        raise SomeOtherDomainError("boom")

    monkeypatch.setattr(jobs, "run_list_accounts", boom)

    response = client.get(
        "/accounts",
        params={"user_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": "s3cret"},  # pragma: allowlist secret
    )

    assert response.status_code == 500


def test_a_settings_error_maps_to_500(monkeypatch):
    app = create_app(_settings())
    client = TestClient(app)

    def boom(**kw):
        raise SettingsError("DATASTORE_API_URL and INTERNAL_API_SECRET must both be set")

    monkeypatch.setattr(jobs, "run_rebuild_returns", boom)

    response = client.post(
        "/returns/rebuild",
        json={"user_id": str(uuid.uuid4()), "account_label": "m"},
        headers={"X-Internal-Secret": "s3cret"},  # pragma: allowlist secret
    )

    assert response.status_code == 500


def test_a_wrong_internal_secret_is_rejected_with_401(monkeypatch):
    app = create_app(_settings())
    client = TestClient(app)
    monkeypatch.setattr(jobs, "run_list_accounts", lambda **kw: {"accounts": []})

    response = client.get(
        "/accounts",
        params={"user_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": "wrong"},  # pragma: allowlist secret
    )

    assert response.status_code == 401


def test_a_missing_internal_secret_header_is_rejected_with_401():
    app = create_app(_settings())
    client = TestClient(app)

    response = client.get("/accounts", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 401


def test_a_correct_internal_secret_is_accepted(monkeypatch):
    app = create_app(_settings())
    client = TestClient(app)
    monkeypatch.setattr(
        jobs,
        "run_list_accounts",
        lambda **kw: {"status": "ok", "rows_saved": 0, "accounts_listed": 0, "accounts": []},
    )

    response = client.get(
        "/accounts",
        params={"user_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": "s3cret"},  # pragma: allowlist secret
    )

    assert response.status_code == 200
