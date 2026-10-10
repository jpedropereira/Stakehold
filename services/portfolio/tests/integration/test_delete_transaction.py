"""DELETE /transactions against a real Postgres, through a real create_app(): the row is really
gone, the follow-on positions rebuild really prunes what it supported, the isolation chokepoint
really keeps another user's account out of reach, and a miss really comes back as a 404. Returns
pruning after a delete is covered in test_rebuild_returns.py; data-store isn't configured here,
so the chained returns rebuild reports `skipped`.
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from portfolio.api.app import create_app
from portfolio.settings import Settings

pytestmark = pytest.mark.integration

_SECRET = "integration-test-secret"  # pragma: allowlist secret
_ISIN = "IE00B4L5Y983"


@pytest.fixture(autouse=True)
def _ticker_mappings(seed_mapping):
    seed_mapping("ISIN", _ISIN, "IWDA.AS")


@pytest.fixture
def client():
    settings = Settings(
        database_url=os.environ["PORTFOLIO_DATABASE_URL"],
        admin_database_url=os.environ["APP_POSTGRES_ADMIN_DATABASE_URL"],
        datastore_api_url=None,
        internal_api_secret=None,
        api_internal_secret=_SECRET,
        api_host="0.0.0.0",  # noqa
        api_port=8002,
    )
    return TestClient(create_app(settings))


_HEADERS = {"X-Internal-Secret": _SECRET}


def _add_account(client, user_id, label="main"):
    response = client.post(
        "/accounts",
        json={"user_id": str(user_id), "broker": "acme", "account_label": label},
        headers=_HEADERS,
    )
    assert response.status_code == 201


def _add_buy(client, user_id, ref, label="main"):
    response = client.post(
        "/transactions",
        json={
            "user_id": str(user_id),
            "account_label": label,
            "broker_transaction_ref": ref,
            "transaction_type": "BUY",
            "trade_datetime": "2026-01-05T10:00:00+00:00",
            "native_amount": "-950.00",
            "native_currency": "EUR",
            "instrument_identifier": _ISIN,
            "instrument_identifier_kind": "ISIN",
            "quantity": "10",
            "price": "95.00",
        },
        headers=_HEADERS,
    )
    assert response.status_code == 201


def _delete(client, user_id, ref, label="main"):
    return client.delete(
        "/transactions",
        params={"user_id": str(user_id), "account_label": label, "broker_transaction_ref": ref},
        headers=_HEADERS,
    )


def _refs(client, user_id, label="main"):
    response = client.get(
        "/transactions",
        params={"user_id": str(user_id), "account_label": label},
        headers=_HEADERS,
    )
    return [t["broker_transaction_ref"] for t in response.json()]


def _positions(client, user_id, label="main"):
    response = client.get(
        "/positions",
        params={"user_id": str(user_id), "account_label": label},
        headers=_HEADERS,
    )
    return response.json()


def test_deleting_a_tickers_only_buy_removes_the_row_and_its_position(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id, "buy-1")
    assert len(_positions(client, user_id)) == 1

    response = _delete(client, user_id, "buy-1")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "transactions_deleted": 1,
        "positions_rebuild": "ok",
        "returns_rebuild": "skipped",
    }
    assert _refs(client, user_id) == []
    assert _positions(client, user_id) == []


def test_deleting_one_of_two_buys_recomputes_the_position(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id, "buy-1")
    _add_buy(client, user_id, "buy-2")

    assert _delete(client, user_id, "buy-1").status_code == 200

    assert _refs(client, user_id) == ["buy-2"]
    [position] = _positions(client, user_id)
    assert float(position["quantity"]) == 10


def test_an_unknown_reference_is_a_404(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)

    response = _delete(client, user_id, "missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "no transaction 'missing' in account 'main'"}


def test_another_users_transaction_is_out_of_reach(client):
    owner, other = uuid.uuid4(), uuid.uuid4()
    _add_account(client, owner)
    _add_buy(client, owner, "buy-1")

    response = _delete(client, other, "buy-1")

    assert response.status_code == 404
    assert _refs(client, owner) == ["buy-1"]


def test_the_same_reference_in_another_account_is_untouched(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id, "a")
    _add_account(client, user_id, "b")
    _add_buy(client, user_id, "same", label="a")
    _add_buy(client, user_id, "same", label="b")

    assert _delete(client, user_id, "same", label="a").status_code == 200

    assert _refs(client, user_id, "a") == []
    assert _refs(client, user_id, "b") == ["same"]
    assert len(_positions(client, user_id, "b")) == 1


def test_a_deleted_reference_can_be_entered_again(client):
    """The reference is the idempotency key, so a mistaken row that's deleted and re-entered
    correctly must save, not be skipped as a duplicate."""
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id, "buy-1")
    _delete(client, user_id, "buy-1")

    _add_buy(client, user_id, "buy-1")

    assert _refs(client, user_id) == ["buy-1"]
