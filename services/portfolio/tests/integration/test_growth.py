"""Growth since inception against live Postgres: pipeline.list_growth's real return_snapshots and
transactions reads (the BUY/SELL type filter at the SQL level, ticker mappings loaded from their
table), and GET /returns/growth through a real create_app() -- the secret check, the JSON shape,
and the 404 handler the unit suite's bare app doesn't have.

Seeds through the real CLI stack. Every add-transaction chains a positions and returns rebuild,
so serving prices from a stub DataStoreClient (monkeypatched in for jobs.HttpDataStoreClient,
the same convention test_rebuild_returns.py uses) is enough to leave return rows behind.
"""

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from portfolio import cli, jobs, pipeline
from portfolio.api.app import create_app
from portfolio.datastore_client.base import PricePoint, TickerInfo
from portfolio.errors import AccountNotFound
from portfolio.returns.performance import GrowthPoint
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
            PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("104.50")),
        ]
    }
    monkeypatch.setattr(
        jobs,
        "HttpDataStoreClient",
        lambda base_url, internal_api_secret: StubDataStoreClient(prices),
    )


def _add(user_id, *, ref, type_, trade_date, extra):
    exit_code = cli.main(
        [
            "add-transaction",
            "--user",
            str(user_id),
            "--account",
            "m",
            "--ref",
            ref,
            "--type",
            type_,
            "--datetime",
            f"{trade_date}T10:00:00+00:00",
            "--currency",
            "EUR",
        ]
        + extra
    )
    assert exit_code == 0


def _seed_buy_and_dividend():
    """A 10 @ 95 buy on 2026-01-05 and a 9.50 dividend on 2026-01-06; the stub prices close at
    95.00 and 104.50, so growth is 0 and then (1045 + 9.50 - 950) / 950 = 0.11."""
    user_id = uuid.uuid4()
    assert (
        cli.main(["account", "add", "--user", str(user_id), "--broker", "acme", "--label", "m"])
        == 0
    )
    instrument = ["--instrument", _ISIN, "--instrument-kind", "ISIN"]
    _add(
        user_id,
        ref="buy",
        type_="BUY",
        trade_date="2026-01-05",
        extra=instrument + ["--quantity", "10", "--price", "95.00", "--amount", "-950.00"],
    )
    _add(
        user_id,
        ref="div",
        type_="DIVIDEND",
        trade_date="2026-01-06",
        extra=instrument + ["--amount", "9.50"],
    )
    return user_id


def _list_growth(pg_connect, user_id):
    with pg_connect() as conn:
        mappings = pipeline.load_ticker_mappings(conn)
        return pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=mappings)


def test_growth_follows_the_stored_rows_and_counts_a_dividend_once(pg_connect):
    user_id = _seed_buy_and_dividend()

    points = _list_growth(pg_connect, user_id)

    # Counting the DIVIDEND as a flow too would give 0.12 on 2026-01-06.
    assert points == [
        GrowthPoint(price_date=date(2026, 1, 5), growth_pct=Decimal("0.00000000")),
        GrowthPoint(price_date=date(2026, 1, 6), growth_pct=Decimal("0.11000000")),
    ]


def test_another_users_account_label_is_not_found(pg_connect):
    owner = uuid.uuid4()
    assert (
        cli.main(["account", "add", "--user", str(owner), "--broker", "acme", "--label", "m"]) == 0
    )

    with pytest.raises(AccountNotFound):
        _list_growth(pg_connect, uuid.uuid4())


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


def test_get_returns_growth_serves_the_series(client):
    user_id = _seed_buy_and_dividend()

    response = client.get(
        "/returns/growth",
        params={"user_id": str(user_id), "account_label": "m"},
        headers={"X-Internal-Secret": _SECRET},
    )

    assert response.status_code == 200
    # str(Decimal("0.00000000")) is "0E-8": the wire format every Decimal field here uses.
    assert response.json() == [
        {"price_date": "2026-01-05", "growth_pct": "0E-8"},
        {"price_date": "2026-01-06", "growth_pct": "0.11000000"},
    ]


def test_get_returns_growth_is_404_for_another_users_account(client):
    _seed_buy_and_dividend()

    response = client.get(
        "/returns/growth",
        params={"user_id": str(uuid.uuid4()), "account_label": "m"},
        headers={"X-Internal-Secret": _SECRET},
    )

    assert response.status_code == 404


def test_get_returns_growth_needs_the_internal_secret(client):
    response = client.get(
        "/returns/growth", params={"user_id": str(uuid.uuid4()), "account_label": "m"}
    )

    assert response.status_code == 401
