"""End-to-end HTTP API tests against a real Postgres, driven through a real create_app() --
the true-round-trip contract the unit suite's monkeypatched jobs.py can't prove: a route really
reaches the database, the isolation chokepoint really blocks another user's account, and the
shared secret really gates every route but /health.
"""

import os
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from portfolio.api.app import create_app
from portfolio.settings import Settings

pytestmark = pytest.mark.integration

_SECRET = "integration-test-secret"  # pragma: allowlist secret
_ISIN = "IE00B4L5Y983"


@pytest.fixture(autouse=True)
def _ticker_mappings(monkeypatch, tmp_path):
    """The default configs/ticker_mappings.yaml is real, gitignored broker data (task 15) and
    doesn't exist in a dev checkout -- same override tests/integration/test_rebuild_positions.py
    uses."""
    path = tmp_path / "ticker_mappings.yaml"
    path.write_text(yaml.dump({"isin": {_ISIN: "IWDA.AS"}}))
    monkeypatch.setenv("PORTFOLIO_TICKER_MAPPINGS_PATH", str(path))
    return path


def _settings():
    # ticker_mappings_path reads the same env var load_settings() would (set per-test by the
    # autouse _ticker_mappings fixture above) -- now that routes pass this Settings object into
    # jobs.py rather than each job re-loading its own, a hardcoded default here would silently
    # stop honouring that override.
    return Settings(
        database_url=os.environ["PORTFOLIO_DATABASE_URL"],
        admin_database_url=os.environ["APP_POSTGRES_ADMIN_DATABASE_URL"],
        ticker_mappings_path=Path(os.environ["PORTFOLIO_TICKER_MAPPINGS_PATH"]),
        datastore_api_url=None,
        internal_api_secret=None,
        api_internal_secret=_SECRET,
        api_host="0.0.0.0",
        api_port=8002,
    )


@pytest.fixture
def client():
    app = create_app(_settings())
    return TestClient(app)


def _headers():
    return {"X-Internal-Secret": _SECRET}


def test_health_reports_ok_against_a_freshly_initialized_database(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_a_missing_internal_secret_is_rejected_on_every_route_but_health(client):
    response = client.get("/accounts", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 401


def test_create_account_then_list_it(client):
    user_id = uuid.uuid4()

    create_response = client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "acme", "account_label": "main"},
        headers=_headers(),
    )
    list_response = client.get("/accounts", params={"user_id": str(user_id)}, headers=_headers())

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    labels = [a["account_label"] for a in list_response.json()]
    assert labels == ["main"]


def test_an_unknown_account_label_returns_404_not_500(client):
    response = client.get(
        "/transactions",
        params={"user_id": str(uuid.uuid4()), "account_label": "nope"},
        headers=_headers(),
    )

    assert response.status_code == 404


def test_a_second_users_account_label_is_not_visible_to_the_first(client):
    owner, other = uuid.uuid4(), uuid.uuid4()
    client.post(
        "/accounts",
        json={"user_id": str(owner), "broker": "acme", "account_label": "shared-label"},
        headers=_headers(),
    )

    response = client.get(
        "/transactions",
        params={"user_id": str(other), "account_label": "shared-label"},
        headers=_headers(),
    )

    assert response.status_code == 404


def test_add_transaction_then_rebuild_positions_then_read_them_back(client):
    user_id = uuid.uuid4()
    client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "acme", "account_label": "main"},
        headers=_headers(),
    )
    client.post(
        "/transactions",
        json={
            "user_id": str(user_id),
            "account_label": "main",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "BUY",
            "trade_datetime": "2026-01-05T10:00:00+00:00",
            "native_amount": "-950.00",
            "native_currency": "EUR",
            "instrument_identifier": _ISIN,
            "instrument_identifier_kind": "ISIN",
            "quantity": "10",
            "price": "95.00",
        },
        headers=_headers(),
    )

    rebuild_response = client.post(
        "/positions/rebuild",
        json={"user_id": str(user_id), "account_label": "main"},
        headers=_headers(),
    )
    positions_response = client.get(
        "/positions",
        params={"user_id": str(user_id), "account_label": "main"},
        headers=_headers(),
    )

    assert rebuild_response.status_code == 201
    assert rebuild_response.json()["status"] == "ok"
    assert positions_response.status_code == 200
    positions = positions_response.json()
    assert len(positions) == 1
    assert positions[0]["canonical_ticker"] == "IWDA.AS"
    assert positions[0]["quantity"] == "10.000000"


def _add_buy(client, user_id, *, ref, identifier):
    return client.post(
        "/transactions",
        json={
            "user_id": str(user_id),
            "account_label": "main",
            "broker_transaction_ref": ref,
            "transaction_type": "BUY",
            "trade_datetime": "2026-01-05T10:00:00+00:00",
            "native_amount": "-950.00",
            "native_currency": "EUR",
            "instrument_identifier": identifier,
            "instrument_identifier_kind": "ISIN",
            "quantity": "10",
            "price": "95.00",
        },
        headers=_headers(),
    )


def test_adding_a_transaction_updates_positions_without_a_manual_rebuild(client):
    user_id = uuid.uuid4()
    client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "acme", "account_label": "main"},
        headers=_headers(),
    )

    add_response = _add_buy(client, user_id, ref="ref-1", identifier=_ISIN)
    positions_response = client.get(
        "/positions",
        params={"user_id": str(user_id), "account_label": "main"},
        headers=_headers(),
    )

    assert add_response.status_code == 201
    assert add_response.json()["positions_rebuild"] == "ok"
    positions = positions_response.json()
    assert len(positions) == 1
    assert positions[0]["canonical_ticker"] == "IWDA.AS"
    assert positions[0]["quantity"] == "10.000000"


def test_an_unmapped_instrument_still_saves_the_transaction_and_reports_a_partial_rebuild(client):
    user_id = uuid.uuid4()
    client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "acme", "account_label": "main"},
        headers=_headers(),
    )

    add_response = _add_buy(client, user_id, ref="ref-1", identifier="XX0000000000")
    transactions_response = client.get(
        "/transactions",
        params={"user_id": str(user_id), "account_label": "main"},
        headers=_headers(),
    )

    assert add_response.status_code == 201
    assert add_response.json()["status"] == "ok"
    assert add_response.json()["positions_rebuild"] == "partial"
    assert len(transactions_response.json()) == 1


class _FakeDataStoreClient:
    """Stands in for data-store, which this suite doesn't run: knows exactly one ticker."""

    def __init__(self, base_url, internal_api_secret):
        pass

    def get_ticker_info(self, ticker):
        from portfolio.datastore_client.base import TickerInfo

        known = ticker == "IWDA.AS"
        return TickerInfo(exists=known, has_returns=known)


@pytest.fixture
def ticker_client(monkeypatch):
    """A client whose Settings point at a (fake) data-store, which TICKER entry requires."""
    from dataclasses import replace

    from portfolio import jobs

    monkeypatch.setattr(jobs, "HttpDataStoreClient", _FakeDataStoreClient)
    settings = replace(
        _settings(),
        datastore_api_url="http://datastore:8000",
        internal_api_secret="unused",  # pragma: allowlist secret
    )
    return TestClient(create_app(settings))


def _add_ticker_buy(client, user_id, *, ticker):
    return client.post(
        "/transactions",
        json={
            "user_id": str(user_id),
            "account_label": "main",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "BUY",
            "trade_datetime": "2026-01-05T10:00:00+00:00",
            "native_amount": "-643.25",
            "native_currency": "EUR",
            "instrument_identifier": ticker,
            "instrument_identifier_kind": "TICKER",
            "quantity": "5",
            "price": "128.65",
        },
        headers=_headers(),
    )


def test_a_ticker_transaction_becomes_a_position_with_no_ticker_mapping(
    ticker_client, _ticker_mappings
):
    # An empty mapping file: the position can only come from the TICKER identifier itself.
    _ticker_mappings.write_text(yaml.dump({"isin": {}}))
    user_id = uuid.uuid4()
    ticker_client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "Degiro", "account_label": "main"},
        headers=_headers(),
    )

    add_response = _add_ticker_buy(ticker_client, user_id, ticker="IWDA.AS")
    positions = ticker_client.get(
        "/positions",
        params={"user_id": str(user_id), "account_label": "main"},
        headers=_headers(),
    ).json()

    assert add_response.status_code == 201
    assert add_response.json()["positions_rebuild"] == "ok"
    assert [(p["canonical_ticker"], p["quantity"]) for p in positions] == [("IWDA.AS", "5.000000")]


def test_a_ticker_data_store_does_not_know_is_rejected_and_not_saved(ticker_client):
    user_id = uuid.uuid4()
    ticker_client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "Degiro", "account_label": "main"},
        headers=_headers(),
    )

    add_response = _add_ticker_buy(ticker_client, user_id, ticker="IWDA.AM")
    transactions = ticker_client.get(
        "/transactions",
        params={"user_id": str(user_id), "account_label": "main"},
        headers=_headers(),
    ).json()

    assert add_response.status_code == 422
    assert "IWDA.AM" in add_response.json()["detail"]
    assert transactions == []


def test_a_buy_entered_without_an_amount_is_saved_with_the_derived_one(ticker_client):
    user_id = uuid.uuid4()
    ticker_client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "Degiro", "account_label": "main"},
        headers=_headers(),
    )

    add_response = ticker_client.post(
        "/transactions",
        json={
            "user_id": str(user_id),
            "account_label": "main",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "BUY",
            "trade_datetime": "2026-01-05T10:00:00+00:00",
            "native_currency": "EUR",
            "instrument_identifier": "IWDA.AS",
            "instrument_identifier_kind": "TICKER",
            "quantity": "5",
            "price": "128.65",
            "fees_native": "2.00",
        },
        headers=_headers(),
    )
    (txn,) = ticker_client.get(
        "/transactions",
        params={"user_id": str(user_id), "account_label": "main"},
        headers=_headers(),
    ).json()

    assert add_response.status_code == 201
    # -(5 * 128.65) - 2.00
    assert txn["native_amount"] == "-645.2500"
    assert txn["eur_amount"] == "-645.2500"
