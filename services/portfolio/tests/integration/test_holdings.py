"""Holdings across accounts against live Postgres: holdings/flows.list_holdings's real
accounts, position_snapshots and return_snapshots reads, and GET /holdings through a real
create_app() -- the secret check and the JSON shape.

Seeds through the real CLI stack. Every add-transaction chains a positions and returns rebuild,
so serving prices from a stub DataStoreClient (monkeypatched in for jobs.HttpDataStoreClient,
the same convention test_growth.py uses) is enough to leave return rows behind.
"""

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from portfolio import cli, jobs
from portfolio.api.app import create_app
from portfolio.datastore_client.base import PricePoint, TickerInfo
from portfolio.holdings import flows
from portfolio.settings import Settings

pytestmark = pytest.mark.integration

_ISIN = "IE00B4L5Y983"
_SECRET = "integration-test-secret"  # pragma: allowlist secret


class StubDataStoreClient:
    def __init__(self, price_series):
        self._price_series = price_series

    def get_price_series(self, ticker, start, end):
        return [p for p in self._price_series.get(ticker, []) if start <= p.date <= end]

    def get_fx_rate_series(self, base, quote, start, end):
        return []

    def get_ticker_info(self, ticker):
        return TickerInfo(exists=True, has_returns=True)


@pytest.fixture(autouse=True)
def _ticker_mappings(seed_mapping):
    seed_mapping("ISIN", _ISIN, "IWDA.AS")


@pytest.fixture(autouse=True)
def _serve_prices(monkeypatch):
    monkeypatch.setenv("DATASTORE_API_URL", "http://stub-datastore")
    monkeypatch.setenv("INTERNAL_API_SECRET", "stub-secret")  # pragma: allowlist secret
    prices = {
        "IWDA.AS": [
            PricePoint(date=date(2026, 1, 5), split_adjusted_close=Decimal("95.00")),
            PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00")),
        ]
    }
    monkeypatch.setattr(
        jobs,
        "HttpDataStoreClient",
        lambda base_url, internal_api_secret: StubDataStoreClient(prices),
    )


def _add_account(user_id, label):
    assert (
        cli.main(["account", "add", "--user", str(user_id), "--broker", "acme", "--label", label])
        == 0
    )


def _buy(user_id, label, *, ref, quantity, amount):
    exit_code = cli.main(
        [
            "add-transaction",
            "--user",
            str(user_id),
            "--account",
            label,
            "--ref",
            ref,
            "--type",
            "BUY",
            "--datetime",
            "2026-01-05T10:00:00+00:00",
            "--currency",
            "EUR",
            "--instrument",
            _ISIN,
            "--instrument-kind",
            "ISIN",
            "--quantity",
            quantity,
            "--price",
            "95.00",
            "--amount",
            amount,
        ]
    )
    assert exit_code == 0


def _seed_two_users():
    """User U: 120 IWDA in A and 20 in B, all bought at 95. User V: 7 IWDA in an account also
    labelled A, which must never reach U's result."""
    owner, other = uuid.uuid4(), uuid.uuid4()
    _add_account(owner, "A")
    _add_account(owner, "B")
    _add_account(other, "A")
    _buy(owner, "A", ref="a", quantity="120", amount="-11400.00")
    _buy(owner, "B", ref="b", quantity="20", amount="-1900.00")
    _buy(other, "A", ref="v", quantity="7", amount="-665.00")
    return owner


def test_holdings_sum_one_users_accounts_and_no_one_elses(pg_connect):
    owner = _seed_two_users()

    with pg_connect() as conn:
        result = flows.list_holdings(conn, user_id=owner)

    assert len(result.holdings) == 1
    row = result.holdings[0]
    assert row.canonical_ticker == "IWDA.AS"
    assert row.quantity == Decimal("140")
    assert row.cost_basis_eur == Decimal("13300")
    assert row.market_value_eur == Decimal("14000")  # 140 x 100.00 on 2026-01-06
    assert row.price_date == date(2026, 1, 6)
    assert row.priced is True
    assert [(e.account_label, e.quantity) for e in row.accounts] == [
        ("A", Decimal("120")),
        ("B", Decimal("20")),
    ]
    assert result.totals.market_value_eur == Decimal("14000")


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


def test_get_holdings_serves_the_combined_rows(client):
    owner = _seed_two_users()

    response = client.get(
        "/holdings", params={"user_id": str(owner)}, headers={"X-Internal-Secret": _SECRET}
    )

    assert response.status_code == 200
    body = response.json()
    assert [(h["canonical_ticker"], Decimal(h["quantity"])) for h in body["holdings"]] == [
        ("IWDA.AS", Decimal("140"))
    ]
    assert Decimal(body["totals"]["total_return_eur"]) == Decimal("700")


def test_get_holdings_for_a_user_with_no_accounts_is_empty(client):
    _seed_two_users()

    response = client.get(
        "/holdings", params={"user_id": str(uuid.uuid4())}, headers={"X-Internal-Secret": _SECRET}
    )

    assert response.status_code == 200
    assert response.json() == {"holdings": [], "totals": None}


def test_get_holdings_needs_the_internal_secret(client):
    response = client.get("/holdings", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 401
