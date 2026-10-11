"""backfill_market_data with a ticker the provider couldn't be reached for: counted as failed,
not as "no data", so the run is `partial` and a caller (ticker onboarding) can say why.

Its own file because test_pipeline.py is already over the repo's 64KB limit."""

import logging
from datetime import date

import pandas as pd
import pytest
from data_store import pipeline
from data_store.config_loader import TickerConfig


def _tracked(ticker):
    return TickerConfig(
        ticker=ticker,
        display_name=ticker,
        asset_type="equity",
        price_source="yfinance",
        enabled=True,
    )


class FakeSource:
    """One ticker per fetch, like backfill's calls. `unreachable_tickers` come back empty and
    are reported in `.unreachable`; `empty_tickers` come back empty and aren't."""

    name = "yfinance"

    def __init__(self, *, unreachable_tickers=(), empty_tickers=()):
        self.unreachable_tickers = set(unreachable_tickers)
        self.empty_tickers = set(empty_tickers)
        self.unreachable = set()

    def fetch(self, tickers, start_date, end_date):
        ticker = tickers[0]
        self.unreachable = {ticker} & self.unreachable_tickers
        if ticker in self.unreachable_tickers | self.empty_tickers:
            return pd.DataFrame()
        return pd.DataFrame({"ticker": [ticker], "price_date": [start_date]})


@pytest.fixture
def backfill(monkeypatch):
    sleeps = []
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", sleeps.append)

    def run(source, tickers):
        monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: source)
        result = pipeline.backfill_market_data(
            object(), [], [_tracked(t) for t in tickers], {}, None, date(2026, 1, 1), 3
        )
        return result, sleeps

    return run


def test_an_unreachable_ticker_is_failed_not_no_data(backfill, caplog):
    caplog.set_level(logging.INFO)
    source = FakeSource(unreachable_tickers={"OFFLINE"})

    result, sleeps = backfill(source, ["OFFLINE", "GOOD"])

    assert result["status"] == "partial"
    assert result["tickers_unreachable"] == 1
    assert result["tickers_failed"] == 1
    assert result["tickers_fetched"] == 1
    assert result["rows_saved"] == 1
    assert "OFFLINE couldn't reach the provider" in caplog.text
    assert "OFFLINE -> no data" not in caplog.text
    # Still a live fetch, so still followed by the politeness delay.
    assert sleeps == [3, 3]


def test_a_ticker_with_no_data_is_still_not_a_failure(backfill):
    source = FakeSource(empty_tickers={"EMPTY"})

    result, _ = backfill(source, ["EMPTY"])

    assert result["status"] == "ok"
    assert result["tickers_unreachable"] == 0
    assert result["tickers_failed"] == 0


def test_the_completion_line_counts_unreachable_tickers(backfill, caplog):
    caplog.set_level(logging.INFO)

    backfill(FakeSource(unreachable_tickers={"OFFLINE"}), ["OFFLINE"])

    assert "1 failed (1 unreachable)" in caplog.text
