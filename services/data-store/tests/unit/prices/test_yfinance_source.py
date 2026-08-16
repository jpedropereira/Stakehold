from datetime import date

import pandas as pd
import pytest
from data_store.prices.yfinance_source import YFinancePriceSource

EXPECTED_COLUMNS = ["ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume"]


def make_yf_frame(tickers, dates):
    idx = pd.DatetimeIndex(dates, name="Date")
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Adj Close", "Volume"], tickers],
        names=["Price", "Ticker"],
    )
    values = [[float(i * 10 + j) for j in range(len(cols))] for i in range(len(dates))]
    return pd.DataFrame(values, index=idx, columns=cols)


def test_name_is_yfinance():
    assert YFinancePriceSource().name == "yfinance"


def test_fetch_reshapes_to_long_form(monkeypatch):
    tickers = ["AAPL", "MSFT"]
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_yf_frame(tickers, dates)

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinancePriceSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert list(result.columns) == EXPECTED_COLUMNS
    assert set(result["ticker"]) == {"AAPL", "MSFT"}
    assert len(result) == len(tickers) * len(dates)


def test_fetch_batches_large_ticker_lists(monkeypatch):
    tickers = [f"T{i}" for i in range(120)]
    calls = []

    def fake_download(batch, **kwargs):
        calls.append(list(batch))
        return make_yf_frame(batch, [date(2026, 1, 1)])

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", fake_download)

    # short range (<=90 days) -> batch size 50, so 120 tickers -> 3 batches
    result = YFinancePriceSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 10))

    assert len(calls) == 3
    assert sum(len(c) for c in calls) == 120
    assert len(result) == 120


def test_fetch_skips_empty_batches_without_retry(monkeypatch):
    calls = {"count": 0}

    def fake_download(batch, **kwargs):
        calls["count"] += 1
        return pd.DataFrame()

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", fake_download)

    result = YFinancePriceSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 3))

    assert calls["count"] == 1
    assert result.empty
    assert list(result.columns) == EXPECTED_COLUMNS


def test_fetch_retries_on_transient_error_then_succeeds(monkeypatch):
    tickers = ["AAPL"]
    dates = [date(2026, 1, 1)]
    frame = make_yf_frame(tickers, dates)
    attempts = {"count": 0}

    def fake_download(batch, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("Connection reset by peer")
        return frame

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", fake_download)
    monkeypatch.setattr("data_store.prices.yfinance_source.time.sleep", lambda s: None)

    result = YFinancePriceSource(max_retries=3).fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert attempts["count"] == 3
    assert len(result) == 1


def test_fetch_does_not_retry_on_non_transient_error(monkeypatch):
    attempts = {"count": 0}

    def fake_download(batch, **kwargs):
        attempts["count"] += 1
        raise ValueError("Some unexpected permanent failure")

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", fake_download)
    monkeypatch.setattr("data_store.prices.yfinance_source.time.sleep", lambda s: None)

    with pytest.raises(ValueError, match="permanent failure"):
        YFinancePriceSource(max_retries=3).fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 3))

    assert attempts["count"] == 1


def test_fetch_raises_after_exhausting_retries(monkeypatch):
    attempts = {"count": 0}

    def fake_download(batch, **kwargs):
        attempts["count"] += 1
        raise TimeoutError("Read timed out")

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", fake_download)
    monkeypatch.setattr("data_store.prices.yfinance_source.time.sleep", lambda s: None)

    with pytest.raises(TimeoutError):
        YFinancePriceSource(max_retries=3).fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 3))

    assert attempts["count"] == 3


def test_fetch_raises_on_empty_tickers():
    with pytest.raises(ValueError, match="tickers"):
        YFinancePriceSource().fetch([], date(2026, 1, 1), date(2026, 1, 3))


def test_fetch_raises_when_start_after_end():
    with pytest.raises(ValueError, match="start_date"):
        YFinancePriceSource().fetch(["AAPL"], date(2026, 1, 5), date(2026, 1, 1))
