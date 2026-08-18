from datetime import date

import numpy as np
import pandas as pd
import pytest
from data_store.dividends.yfinance_source import YFinanceDividendSource

EXPECTED_COLUMNS = ["ticker", "ex_date", "amount"]


def make_yf_frame(tickers, dates, dividends=None):
    """dividends: {(ticker, date): amount}, defaulting to NaN (yfinance's own padding for
    non-dividend days) for any (ticker, date) not given."""
    dividends = dividends or {}
    idx = pd.DatetimeIndex(dates, name="Date")
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Dividends", "Volume"], tickers],
        names=["Price", "Ticker"],
    )
    values = [[float(i * 10 + j) for j in range(len(cols))] for i in range(len(dates))]
    frame = pd.DataFrame(values, index=idx, columns=cols)

    for ticker in tickers:
        frame[("Dividends", ticker)] = np.nan
    for (ticker, d), amount in dividends.items():
        frame.loc[pd.Timestamp(d), ("Dividends", ticker)] = amount

    return frame


def test_name_is_yfinance():
    assert YFinanceDividendSource().name == "yfinance"


def test_fetch_reshapes_to_long_form(monkeypatch):
    tickers = ["CSU.TO", "BABA"]
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_yf_frame(tickers, dates, dividends={("CSU.TO", date(2026, 1, 1)): 1.35})

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinanceDividendSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert list(result.columns) == EXPECTED_COLUMNS
    assert len(result) == 1
    row = result.iloc[0]
    assert row["ticker"] == "CSU.TO"
    assert row["ex_date"] == date(2026, 1, 1)
    assert row["amount"] == 1.35


def test_fetch_drops_nan_dividend_rows(monkeypatch):
    tickers = ["CSU.TO"]
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_yf_frame(tickers, dates, dividends={("CSU.TO", date(2026, 1, 1)): 1.35})

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinanceDividendSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert len(result) == 1
    assert result.iloc[0]["ex_date"] == date(2026, 1, 1)


def test_fetch_drops_zero_dividend_rows(monkeypatch):
    tickers = ["IWDA.AS"]
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_yf_frame(tickers, dates, dividends={("IWDA.AS", date(2026, 1, 1)): 0.0})

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinanceDividendSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert result.empty
    assert list(result.columns) == EXPECTED_COLUMNS


def test_fetch_reshapes_single_ticker_batch_correctly(monkeypatch):
    # Real yfinance still returns a two-level column MultiIndex even for a single-ticker
    # yf.download(["CSU.TO"], actions=True) request (verified live) -- this guards against
    # a future yfinance version collapsing that level for the single-ticker case.
    tickers = ["CSU.TO"]
    dates = [date(2026, 1, 1)]
    frame = make_yf_frame(tickers, dates, dividends={("CSU.TO", date(2026, 1, 1)): 1.38})

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinanceDividendSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert len(result) == 1
    assert result.iloc[0]["ticker"] == "CSU.TO"
    assert result.iloc[0]["amount"] == 1.38


def test_fetch_batches_large_ticker_lists(monkeypatch):
    tickers = [f"T{i}" for i in range(120)]
    calls = []

    def fake_download(batch, **kwargs):
        calls.append(list(batch))
        return make_yf_frame(batch, [date(2026, 1, 1)])

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", fake_download)

    # short range (<=90 days) -> batch size 50, so 120 tickers -> 3 batches
    result = YFinanceDividendSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 10))

    assert len(calls) == 3
    assert sum(len(c) for c in calls) == 120
    assert result.empty  # no dividends seeded in this fixture


def test_fetch_skips_empty_batches_without_retry(monkeypatch):
    calls = {"count": 0}

    def fake_download(batch, **kwargs):
        calls["count"] += 1
        return pd.DataFrame()

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", fake_download)

    result = YFinanceDividendSource().fetch(["CSU.TO"], date(2026, 1, 1), date(2026, 1, 3))

    assert calls["count"] == 1
    assert result.empty
    assert list(result.columns) == EXPECTED_COLUMNS


def test_fetch_retries_on_transient_error_then_succeeds(monkeypatch):
    tickers = ["CSU.TO"]
    dates = [date(2026, 1, 1)]
    frame = make_yf_frame(tickers, dates, dividends={("CSU.TO", date(2026, 1, 1)): 1.38})
    attempts = {"count": 0}

    def fake_download(batch, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("Connection reset by peer")
        return frame

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", fake_download)
    monkeypatch.setattr("data_store.dividends.yfinance_source.time.sleep", lambda s: None)

    result = YFinanceDividendSource(max_retries=3).fetch(
        tickers, date(2026, 1, 1), date(2026, 1, 3)
    )

    assert attempts["count"] == 3
    assert len(result) == 1


def test_fetch_does_not_retry_on_non_transient_error(monkeypatch):
    attempts = {"count": 0}

    def fake_download(batch, **kwargs):
        attempts["count"] += 1
        raise ValueError("Some unexpected permanent failure")

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", fake_download)
    monkeypatch.setattr("data_store.dividends.yfinance_source.time.sleep", lambda s: None)

    with pytest.raises(ValueError, match="permanent failure"):
        YFinanceDividendSource(max_retries=3).fetch(["CSU.TO"], date(2026, 1, 1), date(2026, 1, 3))

    assert attempts["count"] == 1


def test_fetch_raises_after_exhausting_retries(monkeypatch):
    attempts = {"count": 0}

    def fake_download(batch, **kwargs):
        attempts["count"] += 1
        raise TimeoutError("Read timed out")

    monkeypatch.setattr("data_store.dividends.yfinance_source.yf.download", fake_download)
    monkeypatch.setattr("data_store.dividends.yfinance_source.time.sleep", lambda s: None)

    with pytest.raises(TimeoutError):
        YFinanceDividendSource(max_retries=3).fetch(["CSU.TO"], date(2026, 1, 1), date(2026, 1, 3))

    assert attempts["count"] == 3


def test_fetch_raises_on_empty_tickers():
    with pytest.raises(ValueError, match="tickers"):
        YFinanceDividendSource().fetch([], date(2026, 1, 1), date(2026, 1, 3))


def test_fetch_raises_when_start_after_end():
    with pytest.raises(ValueError, match="start_date"):
        YFinanceDividendSource().fetch(["CSU.TO"], date(2026, 1, 5), date(2026, 1, 1))
