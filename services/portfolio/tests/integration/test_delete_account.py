"""DELETE /accounts against a real Postgres, through a real create_app(): the foreign keys to
`accounts` don't cascade, so this is what proves the delete removes the rows that reference the
account in an order Postgres accepts, all in one transaction. Also covers what a fake can't: the
other account's rows are untouched, another user's account is out of reach, and the label is
free to use again afterwards.

data-store isn't configured here, so return_snapshots rows are inserted directly rather than
produced by a returns rebuild.
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
_HEADERS = {"X-Internal-Secret": _SECRET}


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


def _seed_return(pg_connect, user_id, label="main"):
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO return_snapshots (account_id, canonical_ticker, price_date, quantity, "
            "market_value_native, market_value_eur, cost_basis_eur, unrealized_return_eur) "
            "SELECT id, 'IWDA.AS', '2026-01-06', 10, 1000, 1000, 950, 50 FROM accounts "
            "WHERE user_id = %s AND account_label = %s",
            (user_id, label),
        )


def _delete(client, user_id, label="main"):
    return client.delete(
        "/accounts",
        params={"user_id": str(user_id), "account_label": label},
        headers=_HEADERS,
    )


def _labels(client, user_id):
    response = client.get("/accounts", params={"user_id": str(user_id)}, headers=_HEADERS)
    return [a["account_label"] for a in response.json()]


def _counts(pg_connect):
    with pg_connect() as conn, conn.cursor() as cur:
        counts = {}
        for table in ("accounts", "transactions", "position_snapshots", "return_snapshots"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 -- fixed table names
            (counts[table],) = cur.fetchone()
    return counts


def test_deleting_an_account_removes_it_and_every_row_recorded_in_it(client, pg_connect):
    user_id = uuid.uuid4()
    for label in ("main", "other"):
        _add_account(client, user_id, label)
        _add_buy(client, user_id, "buy-1", label)
        _seed_return(pg_connect, user_id, label)
    _add_buy(client, user_id, "buy-2", "main")

    response = _delete(client, user_id)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "accounts_deleted": 1, "transactions_deleted": 2}
    assert _labels(client, user_id) == ["other"]
    # Only "other" is left: its account, its one transaction, position and return.
    assert _counts(pg_connect) == {
        "accounts": 1,
        "transactions": 1,
        "position_snapshots": 1,
        "return_snapshots": 1,
    }


def test_an_unknown_account_is_a_404(client):
    response = _delete(client, uuid.uuid4(), "missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "no account 'missing' for this user"}


def test_another_users_account_is_out_of_reach(client, pg_connect):
    owner, other = uuid.uuid4(), uuid.uuid4()
    _add_account(client, owner)
    _add_buy(client, owner, "buy-1")

    response = _delete(client, other)

    assert response.status_code == 404
    assert _labels(client, owner) == ["main"]
    assert _counts(pg_connect)["transactions"] == 1


def test_a_deleted_accounts_label_can_be_used_again(client, pg_connect):
    """A fresh account under the old label starts empty: nothing of the deleted one comes back."""
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id, "buy-1")
    _delete(client, user_id)

    _add_account(client, user_id)

    assert _labels(client, user_id) == ["main"]
    transactions = client.get(
        "/transactions",
        params={"user_id": str(user_id), "account_label": "main"},
        headers=_HEADERS,
    )
    assert transactions.json() == []
    assert _counts(pg_connect)["position_snapshots"] == 0
