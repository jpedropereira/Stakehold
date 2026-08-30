import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.api.app import create_app
from data_store.config_loader import CurrenciesConfig, CurrencyConfig
from data_store.fx.repository import get_dates_with_rate, get_rate_series, save_fx_rates
from data_store.market_data.repository import save_raw_market_data
from data_store.read_flows import fx_rate_series
from data_store.returns.repository import (
    get_return_series,
    get_returns_date_range,
    save_market_returns,
    ticker_exists,
)
from fastapi.testclient import TestClient
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"
TICKER = "TEST_READAPI"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_prices_df(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "price_date",
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "dividends",
            "stock_splits",
        ],
    )


def make_returns_df(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "price_date",
            "split_adjusted_close",
            "future_split_factor",
            "price_return",
            "dividend_return",
            "simple_return",
            "log_return",
        ],
    )


def make_fx_df(rows):
    return pd.DataFrame(rows, columns=["base_currency", "quote_currency", "rate_date", "rate"])


@pytest.fixture(autouse=True)
def clean_tables():
    def clean():
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM market_returns WHERE ticker LIKE 'TEST%%'")
            cur.execute("DELETE FROM raw_market_data WHERE ticker LIKE 'TEST%%'")
            cur.execute("DELETE FROM fx_rates WHERE base_currency = %s", ("TST",))
        conn.commit()
        conn.close()

    clean()
    yield
    clean()


# --- read functions against a live schema, past what a fake cursor can prove -------------------


def test_ticker_exists_and_get_returns_date_range_against_a_real_schema():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df([[TICKER, date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 10.0, 100, 0.0, 0.0]]),
    )
    save_market_returns(
        conn,
        "yfinance",
        make_returns_df(
            [[TICKER, date(2024, 1, 2), 10.0, 1.0, None, None, None, None]],
        ),
    )

    assert ticker_exists(conn, TICKER, "yfinance") is True
    assert ticker_exists(conn, "TEST_DOES_NOT_EXIST", "yfinance") is False
    assert get_returns_date_range(conn, TICKER, "yfinance") == (date(2024, 1, 2), date(2024, 1, 2))
    conn.close()


def test_get_return_series_preserves_null_returns_from_a_real_numeric_column():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df([[TICKER, date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 10.0, 100, 0.0, 0.0]]),
    )
    save_market_returns(
        conn,
        "yfinance",
        make_returns_df([[TICKER, date(2024, 1, 2), 10.0, 1.0, None, None, None, None]]),
    )

    (row,) = get_return_series(conn, TICKER, "yfinance", date(2024, 1, 1), date(2024, 1, 31))

    assert row[0] == date(2024, 1, 2)
    assert row[3] is None  # price_return -- NULL survives a real NUMERIC round trip
    conn.close()


def test_get_rate_series_and_get_dates_with_rate_against_a_real_schema():
    conn = _connect()
    # ZZZ, not a real quote currency: get_dates_with_rate matches on either side of the pair,
    # so a real tracked code like USD would also match this dev database's actual ingested
    # EUR/USD history and break the test's isolation from real data.
    save_fx_rates(
        conn,
        "yfinance",
        make_fx_df(
            [
                ["TST", "ZZZ", date(2024, 1, 2), 1.10],
                ["TST", "GBP", date(2024, 1, 2), 0.85],
            ]
        ),
    )

    direct = get_rate_series(conn, "TST", "ZZZ", "yfinance", date(2024, 1, 1), date(2024, 1, 31))
    dates = get_dates_with_rate(conn, "ZZZ", "yfinance", date(2024, 1, 1), date(2024, 1, 31))

    (row,) = direct
    assert row[0] == date(2024, 1, 2)
    assert float(row[1]) == pytest.approx(1.10)
    assert dates == [date(2024, 1, 2)]
    conn.close()


def test_fx_rate_series_triangulates_against_a_real_schema():
    """rate(USD, GBP) = rate(TST, GBP) / rate(TST, USD) -- the same formula schema.md documents
    for cross-rates, exercised end to end against real Postgres rows rather than fakes."""
    conn = _connect()
    save_fx_rates(
        conn,
        "yfinance",
        make_fx_df(
            [
                ["TST", "USD", date(2024, 1, 2), 1.10],
                ["TST", "GBP", date(2024, 1, 2), 0.85],
            ]
        ),
    )
    currencies_config = CurrenciesConfig(
        base_currency="TST",
        currencies=[
            CurrencyConfig(code="TST", enabled=True),
            CurrencyConfig(code="USD", enabled=True),
            CurrencyConfig(code="GBP", enabled=True),
        ],
    )

    result = fx_rate_series(
        conn, currencies_config, "USD", "GBP", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert result["derivation"] == "triangulated"
    assert result["via"] == "TST"
    (point,) = result["points"]
    assert point["date"] == date(2024, 1, 2)
    assert float(point["rate"]) == pytest.approx(0.85 / 1.10)
    conn.close()


# --- the whole stack through the real ASGI app --------------------------------------------


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "integration-test-secret")
    app = create_app()
    return TestClient(app)


def test_health_needs_no_auth(api_client):
    assert api_client.get("/health").status_code == 200


def test_prices_endpoint_rejects_a_request_with_no_bearer_token(api_client):
    response = api_client.get(
        f"/prices/{TICKER}?start=2024-01-01&end=2024-01-31", follow_redirects=True
    )

    assert response.status_code == 401


def test_prices_endpoint_returns_a_real_series_end_to_end(api_client):
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df([[TICKER, date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 10.0, 100, 0.0, 0.0]]),
    )
    save_market_returns(
        conn,
        "yfinance",
        make_returns_df([[TICKER, date(2024, 1, 2), 10.0, 1.0, None, None, None, None]]),
    )
    conn.close()

    response = api_client.get(
        f"/prices/{TICKER}?start=2024-01-01&end=2024-01-31",
        headers={"Authorization": "Bearer integration-test-secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["points"][0]["split_adjusted_close"] == "10.0000"
    assert body["points"][0]["price_return"] is None


def test_prices_endpoint_404s_for_an_unknown_ticker(api_client):
    response = api_client.get(
        "/prices/TEST_TOTALLY_UNKNOWN?start=2024-01-01&end=2024-01-31",
        headers={"Authorization": "Bearer integration-test-secret"},
    )

    assert response.status_code == 404


def test_tickers_endpoint_reports_exists_false_for_an_unknown_ticker(api_client):
    response = api_client.get(
        "/tickers/TEST_TOTALLY_UNKNOWN",
        headers={"Authorization": "Bearer integration-test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["exists"] is False
