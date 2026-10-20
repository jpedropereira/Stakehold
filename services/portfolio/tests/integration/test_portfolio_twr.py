"""The portfolio TWR against live Postgres: twr/flows.compute_portfolio_twr's real accounts,
return_snapshots and position_snapshots reads, and GET /returns/portfolio/twr through a real
create_app() -- the secret check and the JSON shape.

Seeds through the real CLI stack, as test_portfolio_returns.py does: every add-transaction
chains a positions and returns rebuild, so a stub DataStoreClient (monkeypatched in for
jobs.HttpDataStoreClient) serving prices is enough to leave return rows behind. The same stub
then serves the TWR's own fetch.
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
from portfolio.settings import Settings
from portfolio.twr import flows

pytestmark = pytest.mark.integration

_ISIN = "IE00B4L5Y983"
_SECRET = "integration-test-secret"  # pragma: allowlist secret
D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)
TODAY = date(2026, 1, 9)

# IWDA.AS: 100, then +5% to 105, then -2% to 102.9.
_PRICES = {
    "IWDA.AS": [
        PricePoint(D1, Decimal("100.00"), None),
        PricePoint(D2, Decimal("105.00"), Decimal("0.05")),
        PricePoint(D3, Decimal("102.90"), Decimal("-0.02")),
    ]
}


class StubDataStoreClient:
    def get_price_series(self, ticker, start, end):
        return [p for p in _PRICES.get(ticker, []) if start <= p.date <= end]

    def get_fx_rate_series(self, base, quote, start, end):
        return []

    def get_ticker_info(self, ticker):
        return TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")


@pytest.fixture(autouse=True)
def _ticker_mappings(seed_mapping):
    seed_mapping("ISIN", _ISIN, "IWDA.AS")


@pytest.fixture(autouse=True)
def _serve_prices(monkeypatch):
    monkeypatch.setenv("DATASTORE_API_URL", "http://stub-datastore")
    monkeypatch.setenv("INTERNAL_API_SECRET", "stub-secret")  # pragma: allowlist secret
    monkeypatch.setattr(
        jobs, "HttpDataStoreClient", lambda base_url, internal_api_secret: StubDataStoreClient()
    )


def _add_account(user_id, label):
    assert (
        cli.main(["account", "add", "--user", str(user_id), "--broker", "acme", "--label", label])
        == 0
    )


def _trade(user_id, label, *, ref, kind, when, quantity, price, amount):
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
            kind,
            "--datetime",
            when,
            "--currency",
            "EUR",
            "--instrument",
            _ISIN,
            "--instrument-kind",
            "ISIN",
            "--quantity",
            quantity,
            "--price",
            price,
            "--amount",
            amount,
        ]
    )
    assert exit_code == 0


def _seed():
    """120 IWDA in account A and 20 in B, all bought on D1. A buys 60 more on D2, which must
    not move the return."""
    owner = uuid.uuid4()
    _add_account(owner, "A")
    _add_account(owner, "B")
    d1, d2 = "2026-01-05T10:00:00+00:00", "2026-01-06T10:00:00+00:00"
    _trade(
        owner, "A", ref="a", kind="BUY", when=d1, quantity="120", price="100.00", amount="-12000.00"
    )
    _trade(
        owner, "B", ref="b", kind="BUY", when=d1, quantity="20", price="100.00", amount="-2000.00"
    )
    _trade(
        owner, "A", ref="a2", kind="BUY", when=d2, quantity="60", price="105.00", amount="-6300.00"
    )
    return owner


def test_two_accounts_and_a_mid_series_buy_give_the_tickers_own_returns(pg_connect):
    owner = _seed()

    with pg_connect() as conn:
        result = flows.compute_portfolio_twr(
            conn, StubDataStoreClient(), user_id=owner, today=TODAY
        )

    # One ticker, so its own returns: +5%, then -2%. Growth 1.05, then 1.05 x 0.98 = 1.029.
    assert result.base_date == D1
    assert [(p.price_date, p.twr_return, p.twr_growth) for p in result.points] == [
        (D2, Decimal("0.05"), Decimal("1.05")),
        (D3, Decimal("-0.02"), Decimal("1.029")),
    ]
    assert result.n_gaps == 0
    assert result.excluded == []


@pytest.fixture
def client():
    settings = Settings(
        database_url=os.environ["PORTFOLIO_DATABASE_URL"],
        admin_database_url=os.environ["APP_POSTGRES_ADMIN_DATABASE_URL"],
        datastore_api_url="http://stub-datastore",
        internal_api_secret="stub-secret",  # pragma: allowlist secret
        api_internal_secret=_SECRET,
        api_host="0.0.0.0",  # noqa
        api_port=8002,
    )
    return TestClient(create_app(settings))


def test_get_returns_portfolio_twr_serves_the_series(client):
    owner = _seed()

    response = client.get(
        "/returns/portfolio/twr",
        params={"user_id": str(owner)},
        headers={"X-Internal-Secret": _SECRET},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["base_date"] == "2026-01-05"
    assert [(p["price_date"], Decimal(p["twr_return"])) for p in body["points"]] == [
        ("2026-01-06", Decimal("0.05")),
        ("2026-01-07", Decimal("-0.02")),
    ]
    assert (body["n_gaps"], body["n_excluded"], body["excluded"]) == (0, 0, [])


def test_get_returns_portfolio_twr_for_a_user_with_no_accounts_is_empty(client):
    _seed()

    response = client.get(
        "/returns/portfolio/twr",
        params={"user_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": _SECRET},
    )

    assert response.status_code == 200
    assert response.json() == {
        "base_date": None,
        "points": [],
        "n_gaps": 0,
        "n_excluded": 0,
        "excluded": [],
    }


def test_get_returns_portfolio_twr_needs_the_internal_secret(client):
    response = client.get("/returns/portfolio/twr", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 401
