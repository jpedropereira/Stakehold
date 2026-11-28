import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.api.app import create_app
from data_store.config_loader import CurrenciesConfig, CurrencyConfig
from data_store.fx.repository import get_dates_with_rate, get_rate_series, save_fx_rates
from data_store.holdings.repository import get_listing_currencies, save_holdings
from data_store.market_data.repository import save_raw_market_data
from data_store.read_flows import fx_rate_series
from data_store.regimes.loader import RegimeConfig
from data_store.regimes.repository import save_regimes
from data_store.returns.repository import (
    get_return_series,
    get_returns_date_range,
    list_tickers,
    save_market_returns,
    ticker_exists,
)
from data_store.risk_free.repository import save_risk_free_rates
from fastapi.testclient import TestClient
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"
TICKER = "TEST_READAPI"
FUND = "test_fund_readapi"
RF_SOURCE = "test_readapi"
AUTH = {"Authorization": "Bearer integration-test-secret"}

HOLDINGS_COLUMNS = [
    "holding_ticker",
    "holding_name",
    "sector",
    "asset_class",
    "market_value",
    "weight_percent",
    "notional_value",
    "shares",
    "price",
    "location",
    "exchange",
    "currency",
]


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


def make_holdings_df(rows):
    """rows: (holding_ticker, asset_class, exchange, currency); the rest is filler."""
    return pd.DataFrame(
        [
            [ticker, "NAME", "Sector", asset_class, 1.0, 1.0, 1.0, 1.0, 1.0, "X", exchange, ccy]
            for ticker, asset_class, exchange, ccy in rows
        ],
        columns=HOLDINGS_COLUMNS,
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
            cur.execute("DELETE FROM etf_holdings WHERE fund_key = %s", (FUND,))
            cur.execute("DELETE FROM tracked_tickers WHERE ticker LIKE 'TEST%%'")
            cur.execute("DELETE FROM risk_free_rates WHERE source = %s", (RF_SOURCE,))
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


def test_list_tickers_is_distinct_sorted_and_scoped_to_the_source():
    """The loose-index-scan query: one entry per ticker however many dates it has, in ticker
    order, and a ticker ingested only under another source is left out. Asserted on this
    test's own TEST_ tickers only -- the database may hold other rows."""
    conn = _connect()
    two_days = [
        ["TEST_LIST_B", date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 10.0, 100, 0.0, 0.0],
        ["TEST_LIST_B", date(2024, 1, 3), 10.0, 10.0, 10.0, 10.0, 10.0, 100, 0.0, 0.0],
        ["TEST_LIST_A", date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 10.0, 100, 0.0, 0.0],
    ]
    save_raw_market_data(conn, "yfinance", "equity", make_prices_df(two_days))
    save_raw_market_data(
        conn,
        "test_other_source",
        "equity",
        make_prices_df(
            [["TEST_LIST_OTHER", date(2024, 1, 2), 1.0, 1.0, 1.0, 1.0, 1.0, 1, 0.0, 0.0]]
        ),
    )

    tickers = list_tickers(conn, "yfinance")
    conn.close()

    ours = [t for t in tickers if t.startswith("TEST_LIST_")]
    assert ours == ["TEST_LIST_A", "TEST_LIST_B"]
    assert tickers == sorted(tickers)


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


def test_get_return_series_joins_each_rows_volume_and_keeps_rows_with_no_raw_row():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df(
            [
                [TICKER, date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 10.0, 0, 0.0, 0.0],
                [TICKER, date(2024, 1, 3), 10.0, 10.0, 10.0, 10.0, 10.0, None, 0.0, 0.0],
            ]
        ),
    )
    save_market_returns(
        conn,
        "yfinance",
        make_returns_df(
            [
                [TICKER, date(2024, 1, 2), 10.0, 1.0, None, None, None, None],
                [TICKER, date(2024, 1, 3), 10.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                [TICKER, date(2024, 1, 4), 10.0, 1.0, 0.0, 0.0, 0.0, 0.0],
            ]
        ),
    )

    rows = get_return_series(conn, TICKER, "yfinance", date(2024, 1, 1), date(2024, 1, 31))

    # Zero is a real zero, a stored NULL stays NULL, and a market_returns row with no
    # raw_market_data row is still served, with a NULL volume.
    assert [(row[0], row[7]) for row in rows] == [
        (date(2024, 1, 2), 0),
        (date(2024, 1, 3), None),
        (date(2024, 1, 4), None),
    ]
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
    assert body["points"][0]["split_adjusted_close"] == "10.000000"
    assert body["points"][0]["price_return"] is None


def test_prices_endpoint_404s_for_an_unknown_ticker(api_client):
    response = api_client.get(
        "/prices/TEST_TOTALLY_UNKNOWN?start=2024-01-01&end=2024-01-31",
        headers={"Authorization": "Bearer integration-test-secret"},
    )

    assert response.status_code == 404


def test_risk_free_endpoint_splices_stored_rows_end_to_end(api_client):
    conn = _connect()
    save_risk_free_rates(
        conn,
        RF_SOURCE,
        pd.DataFrame(
            [
                ["eonia", date(1999, 1, 4), 3.200],
                ["eonia", date(2019, 9, 30), -0.468],
                # EONIA after the cutoff is stored but never served.
                ["eonia", date(2019, 10, 1), -0.464],
                ["eonia", date(2019, 10, 2), -0.465],
                ["estr", date(2019, 10, 1), -0.549],
            ],
            columns=["series", "rate_date", "rate"],
        ),
    )
    conn.close()

    response = api_client.get(
        f"/rates/risk-free?start=2019-09-27&end=2019-10-04&source={RF_SOURCE}", headers=AUTH
    )

    assert response.status_code == 200
    assert response.json() == {
        "source": RF_SOURCE,
        "first_date": "1999-01-04",
        "points": [
            {"date": "2019-09-30", "rate": "-0.5530", "series": "eonia_adjusted"},
            {"date": "2019-10-01", "rate": "-0.5490", "series": "estr"},
        ],
    }


@pytest.fixture
def regimes_api(api_client):
    """`regimes` has no test-row prefix, so this empties the whole (test-database) table."""
    conn = _connect()
    save_regimes(
        conn,
        [
            RegimeConfig(
                regime_id="ea_rec_2020",
                dimension="business_cycle",
                scope="EA",
                name="Euro area COVID recession",
                start_date=date(2020, 1, 1),
                end_date=date(2020, 6, 30),
                known_from=date(2021, 11, 9),
                severity="severe",
                source="CEPR-EABCDC",
                notes=None,
            ),
            RegimeConfig(
                regime_id="spx_bear_2007",
                dimension="drawdown",
                scope="SPX",
                name="GFC bear market",
                start_date=date(2007, 10, 9),
                end_date=date(2009, 3, 9),
                known_from=None,
                severity="extreme",
                source="manual_approx",
                notes=None,
            ),
        ],
    )
    conn.close()
    yield api_client
    conn = _connect()
    save_regimes(conn, [])
    conn.close()


def test_regimes_endpoint_needs_the_bearer_token(regimes_api):
    assert regimes_api.get("/regimes?start=2020-01-01&end=2020-12-31").status_code == 401


def test_regimes_endpoint_serves_overlapping_rows_end_to_end(regimes_api):
    response = regimes_api.get("/regimes?start=2020-06-30&end=2020-12-31", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "total_regimes": 2,
        "regimes": [
            {
                "regime_id": "ea_rec_2020",
                "dimension": "business_cycle",
                "scope": "EA",
                "name": "Euro area COVID recession",
                "start_date": "2020-01-01",
                "end_date": "2020-06-30",
                "known_from": "2021-11-09",
                "severity": "severe",
                "source": "CEPR-EABCDC",
                "notes": None,
            }
        ],
    }


def test_tickers_endpoint_reports_exists_false_for_an_unknown_ticker(api_client):
    response = api_client.get(
        "/tickers/TEST_TOTALLY_UNKNOWN",
        headers={"Authorization": "Bearer integration-test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["exists"] is False


# --- a ticker's currency (LB-1) -------------------------------------------------------------


def test_get_listing_currencies_takes_each_listings_newest_snapshot_against_a_real_schema():
    conn = _connect()
    save_holdings(
        conn,
        FUND,
        date(2026, 1, 1),
        make_holdings_df(
            [
                ("TESTMOVED", "Equity", "NASDAQ", "USD"),
                ("TESTGONE", "Equity", "NASDAQ", "USD"),
            ]
        ),
    )
    save_holdings(
        conn,
        FUND,
        date(2026, 2, 1),
        make_holdings_df(
            [
                ("TESTMOVED", "Equity", "NASDAQ", "CAD"),
                # Same raw symbol, another exchange: a different listing, kept apart.
                ("TESTMOVED", "Equity", "Xetra", "EUR"),
                ("TESTFUT", "Futures", "NASDAQ", "USD"),
                ("TESTNOCCY", "Equity", "NASDAQ", None),
                (None, "Cash", "-", "USD"),
            ]
        ),
    )

    listings = [row for row in get_listing_currencies(conn) if row[0].startswith("TEST")]
    conn.close()

    assert sorted(listings) == [
        # A holding that has left the fund still has a currency, from the last snapshot with it.
        ("TESTGONE", "NASDAQ", "USD"),
        ("TESTMOVED", "NASDAQ", "CAD"),
        ("TESTMOVED", "Xetra", "EUR"),
    ]
    # Newest first, so the first match for a priced ticker is the most recent one.
    assert listings.index(("TESTGONE", "NASDAQ", "USD")) == len(listings) - 1


@pytest.fixture
def currency_api(monkeypatch, tmp_path):
    suffixes = tmp_path / "suffixes.yaml"
    suffixes.write_text(
        "exchanges:\n"
        '  - exchange: "Nyse Euronext - Euronext Paris"\n'
        '    suffix: ".PA"\n'
        '  - exchange: "New York Stock Exchange Inc."\n'
        "    suffix: null\n"
    )
    monkeypatch.setenv("DATA_STORE_EXCHANGE_SUFFIXES_PATH", str(suffixes))
    monkeypatch.setenv("INTERNAL_API_SECRET", "integration-test-secret")
    return TestClient(create_app())


def test_ticker_endpoint_reports_a_tracked_tickers_currency_end_to_end(currency_api):
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tracked_tickers"
            " (ticker, display_name, asset_type, currency, origin, status)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            ("TESTVOD.L", "Test Vodafone", "equity", "GBp", "user", "ready"),
        )
    conn.commit()
    conn.close()

    body = currency_api.get("/tickers/TESTVOD.L", headers=AUTH).json()

    assert (body["currency"], body["fx_currency"]) == ("GBp", "GBP")


def test_ticker_endpoint_reports_a_holdings_only_tickers_currency_end_to_end(currency_api):
    conn = _connect()
    save_holdings(
        conn,
        FUND,
        date(2026, 2, 1),
        make_holdings_df(
            [
                ("TESTDG", "Equity", "Nyse Euronext - Euronext Paris", "EUR"),
                ("TESTDG", "Equity", "New York Stock Exchange Inc.", "USD"),
            ]
        ),
    )
    conn.close()

    paris = currency_api.get("/tickers/TESTDG.PA", headers=AUTH).json()
    new_york = currency_api.get("/tickers/TESTDG", headers=AUTH).json()

    assert (paris["currency"], paris["fx_currency"]) == ("EUR", "EUR")
    assert (new_york["currency"], new_york["fx_currency"]) == ("USD", "USD")


def test_ticker_endpoint_reports_no_currency_when_nothing_knows_it(currency_api):
    body = currency_api.get("/tickers/TEST_TOTALLY_UNKNOWN", headers=AUTH).json()

    assert (body["currency"], body["fx_currency"]) == (None, None)


@pytest.fixture
def holdings_api(monkeypatch, tmp_path):
    etfs = tmp_path / "etfs.yaml"
    etfs.write_text(
        "funds:\n"
        f"  - key: {FUND}\n"
        '    display_name: "Test World"\n'
        "    provider: ishares\n"
        '    url: "https://example.com/fund"\n'
        "    isin: null\n"
        "    ticker: TESTIWDA.AS\n"
        "    price_source: yfinance\n"
        "    enabled: true\n"
    )
    suffixes = tmp_path / "suffixes.yaml"
    suffixes.write_text(
        "exchanges:\n"
        '  - exchange: "London Stock Exchange"\n'
        '    suffix: ".L"\n'
        '  - exchange: "NASDAQ"\n'
        "    suffix: null\n"
    )
    monkeypatch.setenv("DATA_STORE_CONFIG_PATH", str(etfs))
    monkeypatch.setenv("DATA_STORE_EXCHANGE_SUFFIXES_PATH", str(suffixes))
    monkeypatch.setenv("INTERNAL_API_SECRET", "integration-test-secret")
    return TestClient(create_app())


def test_etf_holdings_endpoints_need_the_bearer_token(holdings_api):
    assert holdings_api.get("/etf-holdings").status_code == 401
    assert holdings_api.get(f"/etf-holdings/{FUND}/latest").status_code == 401


def test_etf_holdings_endpoints_serve_the_latest_snapshot_end_to_end(holdings_api):
    conn = _connect()
    save_holdings(
        conn, FUND, date(2026, 1, 1), make_holdings_df([("TESTOLD", "Equity", "NASDAQ", "USD")])
    )
    save_holdings(
        conn,
        FUND,
        date(2026, 2, 1),
        make_holdings_df(
            [
                ("TESTBP.", "Equity", "London Stock Exchange", "GBP"),
                (None, "Cash", "-", "USD"),
            ]
        ),
    )
    conn.close()

    funds = holdings_api.get("/etf-holdings", headers=AUTH).json()
    latest = holdings_api.get(f"/etf-holdings/{FUND}/latest", headers=AUTH).json()

    assert funds == {
        "funds": [
            {
                "fund_key": FUND,
                "display_name": "Test World",
                "ticker": "TESTIWDA.AS",
                "enabled": True,
                "latest_as_of_date": "2026-02-01",
            }
        ]
    }
    assert (latest["ticker"], latest["as_of_date"]) == ("TESTIWDA.AS", "2026-02-01")
    # The newest snapshot only, cash line included, each line resolved as ingest would price it.
    assert sorted(
        (c["ticker"] or "", c["yfinance_ticker"], c["weight_percent"])
        for c in latest["constituents"]
    ) == [("", None, "1.0000"), ("TESTBP.", "TESTBP.L", "1.0000")]


def test_etf_holdings_endpoint_404s_for_a_configured_fund_never_scraped(holdings_api):
    response = holdings_api.get(f"/etf-holdings/{FUND}/latest", headers=AUTH)

    assert response.status_code == 404
    assert response.json() == {"detail": f"no holdings snapshot stored: {FUND}"}
