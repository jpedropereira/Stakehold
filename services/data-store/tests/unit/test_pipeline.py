from datetime import date

import pandas as pd
import pytest
from data_store import pipeline
from data_store.config_loader import FundConfig


def make_fund(*, provider="ishares", price_source="yfinance", key="test_fund"):
    return FundConfig(
        key=key,
        display_name="Test Fund",
        provider=provider,
        url="https://example.com/fund",
        isin=None,
        price_source=price_source,
        enabled=True,
    )


class FakePriceSource:
    name = "fake_source"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append((tickers, start_date, end_date))
        return pd.DataFrame({"ticker": tickers, "price_date": [start_date] * len(tickers)})


def test_ingest_holdings_scrapes_fetches_normalizes_and_saves(monkeypatch):
    fund = make_fund()
    conn = object()
    resolved_urls = []
    fetched_urls = []
    normalize_inputs = []
    save_calls = []

    def fake_resolve_url(url):
        resolved_urls.append(url)
        return "https://example.com/holdings.csv"

    def fake_fetch(csv_url):
        fetched_urls.append(csv_url)
        return pd.DataFrame({"raw": [1]})

    def fake_normalize(df):
        normalize_inputs.append(df)
        return pd.DataFrame({"holding_ticker": ["AAPL"]})

    def fake_save(conn_arg, fund_key, as_of_date, df):
        save_calls.append((conn_arg, fund_key, as_of_date, df))
        return len(df)

    monkeypatch.setitem(pipeline.SCRAPERS, "ishares", fake_resolve_url)
    monkeypatch.setattr(pipeline, "fetch_holdings_csv", fake_fetch)
    monkeypatch.setattr(pipeline, "normalize_holdings", fake_normalize)
    monkeypatch.setattr(pipeline, "save_holdings", fake_save)

    count = pipeline.ingest_holdings(conn, fund, date(2026, 1, 1))

    assert count == 1
    assert resolved_urls == [fund.url]
    assert fetched_urls == ["https://example.com/holdings.csv"]
    assert len(normalize_inputs) == 1
    assert len(save_calls) == 1
    assert save_calls[0][:3] == (conn, fund.key, date(2026, 1, 1))
    assert save_calls[0][3]["holding_ticker"].tolist() == ["AAPL"]


def test_ingest_holdings_raises_on_unknown_provider():
    fund = make_fund(provider="unknown_provider")

    with pytest.raises(ValueError, match="unknown_provider"):
        pipeline.ingest_holdings(object(), fund, date(2026, 1, 1))


def test_ingest_daily_ohlcv_reads_tickers_from_repository_not_scrape_result(monkeypatch):
    fund = make_fund()
    conn = object()
    get_tickers_calls = []
    fake_source = FakePriceSource()
    save_calls = []

    def fake_get_latest_tickers(conn_arg, fund_key):
        get_tickers_calls.append((conn_arg, fund_key))
        return ["AAPL", "MSFT"]

    def fake_save_prices(conn_arg, source_name, df):
        save_calls.append((conn_arg, source_name, df))
        return len(df)

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "save_prices", fake_save_prices)

    count = pipeline.ingest_daily_ohlcv(conn, fund, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 2
    assert get_tickers_calls == [(conn, fund.key)]
    assert fake_source.fetch_calls == [(["AAPL", "MSFT"], date(2026, 1, 1), date(2026, 1, 10))]
    assert save_calls[0][1] == "fake_source"


def test_ingest_daily_ohlcv_dedupes_and_strips_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda conn_arg, fund_key: [" AAPL", "AAPL", "MSFT ", "", None],
    )
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "save_prices", lambda conn_arg, source_name, df: len(df))

    pipeline.ingest_daily_ohlcv(conn, fund, date(2026, 1, 1), date(2026, 1, 10))

    fetched_tickers = fake_source.fetch_calls[0][0]
    assert fetched_tickers == ["AAPL", "MSFT"]


def test_ingest_daily_ohlcv_returns_zero_when_no_tickers(monkeypatch):
    fund = make_fund()
    conn = object()

    monkeypatch.setattr(pipeline, "get_latest_tickers", lambda conn_arg, fund_key: [])

    count = pipeline.ingest_daily_ohlcv(conn, fund, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 0


def test_ingest_daily_ohlcv_returns_zero_when_price_source_has_no_data(monkeypatch):
    fund = make_fund()
    conn = object()

    class EmptyPriceSource:
        name = "empty_source"

        def fetch(self, tickers, start_date, end_date):
            return pd.DataFrame()

    monkeypatch.setattr(pipeline, "get_latest_tickers", lambda conn_arg, fund_key: ["AAPL"])
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: EmptyPriceSource())
    save_calls = []
    monkeypatch.setattr(pipeline, "save_prices", lambda *a: save_calls.append(a) or len(a[-1]))

    count = pipeline.ingest_daily_ohlcv(conn, fund, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 0
    assert save_calls == []


def test_ingest_daily_ohlcv_raises_on_unknown_price_source(monkeypatch):
    fund = make_fund(price_source="unknown_source")
    conn = object()

    monkeypatch.setattr(pipeline, "get_latest_tickers", lambda conn_arg, fund_key: ["AAPL"])

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.ingest_daily_ohlcv(conn, fund, date(2026, 1, 1), date(2026, 1, 10))
