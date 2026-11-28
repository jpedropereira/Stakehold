"""PUT /transactions against a real Postgres, through a real create_app(): the row is really
rewritten in place under its key, the follow-on positions rebuild really follows the edit, the
isolation chokepoint really keeps another user's account out of reach, and a miss really comes
back as a 404. data-store isn't configured here, so instruments are ISINs (a TICKER would need it
for its check) and the chained returns rebuild reports `skipped`.
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
_OTHER_ISIN = "IE00B5BMR087"


@pytest.fixture(autouse=True)
def _ticker_mappings(seed_mapping):
    seed_mapping("ISIN", _ISIN, "IWDA.AS")
    seed_mapping("ISIN", _OTHER_ISIN, "CSPX.AS")


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


def _buy(user_id, ref="buy-1", label="main", **overrides):
    body = {
        "user_id": str(user_id),
        "account_label": label,
        "broker_transaction_ref": ref,
        "transaction_type": "BUY",
        "trade_datetime": "2026-01-05T10:00:00+00:00",
        "native_currency": "EUR",
        "instrument_identifier": _ISIN,
        "instrument_identifier_kind": "ISIN",
        "quantity": "10",
        "price": "95.00",
    }
    body.update(overrides)
    return body


def _add_buy(client, user_id, **overrides):
    response = client.post("/transactions", json=_buy(user_id, **overrides), headers=_HEADERS)
    assert response.status_code == 201


def _edit(client, user_id, **overrides):
    return client.put("/transactions", json=_buy(user_id, **overrides), headers=_HEADERS)


def _transactions(client, user_id, label="main"):
    response = client.get(
        "/transactions",
        params={"user_id": str(user_id), "account_label": label},
        headers=_HEADERS,
    )
    return response.json()


def _positions(client, user_id, label="main"):
    response = client.get(
        "/positions",
        params={"user_id": str(user_id), "account_label": label},
        headers=_HEADERS,
    )
    return response.json()


def test_editing_a_buys_quantity_rewrites_the_row_and_its_position(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id)

    response = _edit(client, user_id, quantity="12")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "transactions_updated": 1,
        "positions_rebuild": "ok",
        "returns_rebuild": "skipped",
    }
    [txn] = _transactions(client, user_id)
    assert txn["broker_transaction_ref"] == "buy-1"
    assert float(txn["quantity"]) == 12
    assert float(txn["native_amount"]) == -1140
    [position] = _positions(client, user_id)
    assert float(position["quantity"]) == 12


def test_editing_the_date_moves_the_position_to_it(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id)

    _edit(client, user_id, trade_datetime="2026-02-10T09:30:00+00:00")

    [txn] = _transactions(client, user_id)
    assert txn["trade_datetime"].startswith("2026-02-10T09:30:00")
    [position] = _positions(client, user_id)
    assert position["as_of_date"] == "2026-02-10"


def test_editing_the_instrument_moves_the_position_to_the_new_ticker(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id)

    _edit(client, user_id, instrument_identifier=_OTHER_ISIN)

    assert [p["canonical_ticker"] for p in _positions(client, user_id)] == ["CSPX.AS"]


def test_an_unchanged_isin_keeps_its_kind_even_when_another_is_sent(client):
    """An instrument left as it was keeps how it was recorded, whatever kind the edit sends, so
    an imported ISIN row stays an ISIN after its price is fixed. (SYMBOL rather than TICKER
    here: a TICKER edit needs data-store configured.)"""
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id)

    response = _edit(client, user_id, price="96.00", instrument_identifier_kind="SYMBOL")

    assert response.status_code == 200

    [txn] = _transactions(client, user_id)
    assert txn["instrument_identifier_kind"] == "ISIN"
    assert float(txn["price"]) == 96


def test_an_edit_that_changes_nothing_reports_nothing_to_do(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id)

    response = _edit(client, user_id)

    assert response.json() == {
        "status": "nothing_to_do",
        "transactions_updated": 0,
        "positions_rebuild": "skipped",
        "returns_rebuild": "skipped",
    }


def test_an_invalid_edit_is_a_422_and_leaves_the_row_alone(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)
    _add_buy(client, user_id)

    response = _edit(client, user_id, quantity="-3")

    assert response.status_code == 422
    [txn] = _transactions(client, user_id)
    assert float(txn["quantity"]) == 10


def test_an_unknown_reference_is_a_404(client):
    user_id = uuid.uuid4()
    _add_account(client, user_id)

    response = _edit(client, user_id, ref="missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "no transaction 'missing' in account 'main'"}


def test_another_users_transaction_is_out_of_reach(client):
    owner, other = uuid.uuid4(), uuid.uuid4()
    _add_account(client, owner)
    _add_buy(client, owner)

    response = _edit(client, other, quantity="12")

    assert response.status_code == 404
    [txn] = _transactions(client, owner)
    assert float(txn["quantity"]) == 10
