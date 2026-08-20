import threading
import time
from datetime import date

import pandas as pd
import pytest
from data_store.market_data.yfinance_source import YFinanceMarketDataSource

EXPECTED_COLUMNS = [
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
]


def make_history_frame(dates, *, dividends=None, stock_splits=None):
    """Shaped like a real single-ticker Ticker.history(auto_adjust=False, actions=True)
    response (verified live against installed yfinance): a DatetimeIndex named 'Date', columns
    Open/High/Low/Close/Adj Close/Volume/Dividends/Stock Splits -- the latter two 0.0 (never
    NaN) on non-event days."""
    dividends = dividends or {}
    stock_splits = stock_splits or {}
    idx = pd.DatetimeIndex(dates, name="Date")
    data = {
        "Open": [100.0 + i for i in range(len(dates))],
        "High": [101.0 + i for i in range(len(dates))],
        "Low": [99.0 + i for i in range(len(dates))],
        "Close": [100.5 + i for i in range(len(dates))],
        "Adj Close": [100.5 + i for i in range(len(dates))],
        "Volume": [1000.0 + i for i in range(len(dates))],
        "Dividends": [dividends.get(d, 0.0) for d in dates],
        "Stock Splits": [stock_splits.get(d, 0.0) for d in dates],
    }
    return pd.DataFrame(data, index=idx)


class FakeTicker:
    """Stands in for yf.Ticker(ticker): .history(**kwargs) returns whatever `responses` wired up
    for that ticker (a frame, a zero-arg callable returning one, or an exception instance to
    raise), and records every call's kwargs. Use .configured({...}) to get an isolated fake per
    test -- the class-level responses/calls would otherwise leak between tests."""

    responses: dict = {}
    calls: list = []

    def __init__(self, ticker):
        self.ticker = ticker

    def history(self, **kwargs):
        type(self).calls.append((self.ticker, kwargs))
        response = type(self).responses[self.ticker]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response()
        return response

    @classmethod
    def configured(cls, responses):
        return type("ConfiguredFakeTicker", (cls,), {"responses": responses, "calls": []})


def test_name_is_yfinance():
    assert YFinanceMarketDataSource().name == "yfinance"


def test_fetch_reshapes_to_long_form(monkeypatch):
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_history_frame(dates, dividends={date(2026, 1, 1): 0.25})
    fake = FakeTicker.configured({"AAPL": frame})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 3))

    assert list(result.columns) == EXPECTED_COLUMNS
    assert len(result) == 2
    assert set(result["ticker"]) == {"AAPL"}
    row = result[result["price_date"] == date(2026, 1, 1)].iloc[0]
    assert row["dividends"] == 0.25
    assert row["stock_splits"] == 0.0


def test_fetch_captures_stock_splits(monkeypatch):
    dates = [date(2026, 1, 1)]
    frame = make_history_frame(dates, stock_splits={date(2026, 1, 1): 4.0})
    fake = FakeTicker.configured({"AAPL": frame})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 2))

    assert result.iloc[0]["stock_splits"] == 4.0


def test_fetch_calls_history_with_auto_adjust_false_and_actions_true(monkeypatch):
    frame = make_history_frame([date(2026, 1, 1)])
    fake = FakeTicker.configured({"AAPL": frame})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 3))

    _, kwargs = fake.calls[0]
    assert kwargs["auto_adjust"] is False
    assert kwargs["actions"] is True


def test_fetch_issues_one_call_per_ticker(monkeypatch):
    tickers = ["AAPL", "MSFT", "0700.HK"]
    frame = make_history_frame([date(2026, 1, 1)])
    fake = FakeTicker.configured({t: frame for t in tickers})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert sorted(t for t, _ in fake.calls) == sorted(tickers)
    assert set(result["ticker"]) == set(tickers)


def test_fetch_omits_ticker_with_no_data(monkeypatch):
    tickers = ["AAPL", "DELISTED"]
    fake = FakeTicker.configured(
        {"AAPL": make_history_frame([date(2026, 1, 1)]), "DELISTED": pd.DataFrame()}
    )
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert set(result["ticker"]) == {"AAPL"}


def test_fetch_retries_on_transient_error_then_succeeds(monkeypatch):
    attempts = {"count": 0}
    frame = make_history_frame([date(2026, 1, 1)])

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("Connection reset by peer")
        return frame

    fake = FakeTicker.configured({"AAPL": flaky})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.market_data.yfinance_source.time.sleep", lambda s: None)

    result = YFinanceMarketDataSource(max_retries=3).fetch(
        ["AAPL"], date(2026, 1, 1), date(2026, 1, 3)
    )

    assert attempts["count"] == 3
    assert len(result) == 1


def test_fetch_omits_ticker_after_exhausting_retries_without_raising(monkeypatch, caplog):
    # A permanently-failing ticker must not kill the whole fetch() call -- unlike the old
    # batched yf.download() design, where one bad ticker in a batch failed the whole batch, a
    # good ticker fetched alongside a bad one still comes back.
    attempts = {"count": 0}

    def always_times_out():
        attempts["count"] += 1
        raise TimeoutError("Read timed out")

    fake = FakeTicker.configured(
        {"BAD": always_times_out, "GOOD": make_history_frame([date(2026, 1, 1)])}
    )
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.market_data.yfinance_source.time.sleep", lambda s: None)

    with caplog.at_level("WARNING"):
        result = YFinanceMarketDataSource(max_retries=3).fetch(
            ["BAD", "GOOD"], date(2026, 1, 1), date(2026, 1, 3)
        )

    assert attempts["count"] == 3
    assert set(result["ticker"]) == {"GOOD"}
    assert "BAD" in caplog.text


def test_fetch_omits_ticker_on_non_transient_error_without_retrying(monkeypatch):
    attempts = {"count": 0}

    def broken():
        attempts["count"] += 1
        raise ValueError("Some unexpected permanent failure")

    fake = FakeTicker.configured({"BAD": broken, "GOOD": make_history_frame([date(2026, 1, 1)])})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.market_data.yfinance_source.time.sleep", lambda s: None)

    result = YFinanceMarketDataSource(max_retries=3).fetch(
        ["BAD", "GOOD"], date(2026, 1, 1), date(2026, 1, 3)
    )

    assert attempts["count"] == 1  # no retry -- not a transient error
    assert set(result["ticker"]) == {"GOOD"}


def test_fetch_returns_empty_frame_with_expected_columns_when_every_ticker_fails(monkeypatch):
    fake = FakeTicker.configured({"BAD": pd.DataFrame()})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(["BAD"], date(2026, 1, 1), date(2026, 1, 3))

    assert result.empty
    assert list(result.columns) == EXPECTED_COLUMNS


def test_fetch_raises_on_empty_tickers():
    with pytest.raises(ValueError, match="tickers"):
        YFinanceMarketDataSource().fetch([], date(2026, 1, 1), date(2026, 1, 3))


def test_fetch_raises_when_start_after_end():
    with pytest.raises(ValueError, match="start_date"):
        YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 5), date(2026, 1, 1))


def test_fetch_respects_max_workers_concurrency_cap(monkeypatch):
    max_workers = 3
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def slow_history():
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
        return make_history_frame([date(2026, 1, 1)])

    tickers = [f"T{i}" for i in range(10)]
    fake = FakeTicker.configured({t: slow_history for t in tickers})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    YFinanceMarketDataSource(max_workers=max_workers).fetch(
        tickers, date(2026, 1, 1), date(2026, 1, 3)
    )

    assert state["peak"] <= max_workers


def test_fetch_drops_rows_with_no_ohlc_at_all(monkeypatch):
    frame = make_history_frame([date(2026, 1, 1), date(2026, 1, 2)])
    frame.loc[pd.Timestamp(date(2026, 1, 2)), ["Open", "High", "Low", "Close"]] = float("nan")
    fake = FakeTicker.configured({"AAPL": frame})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 3))

    assert len(result) == 1
    assert result.iloc[0]["price_date"] == date(2026, 1, 1)


def test_fetch_keeps_rows_missing_only_volume(monkeypatch):
    frame = make_history_frame([date(2026, 1, 1)])
    frame.loc[pd.Timestamp(date(2026, 1, 1)), "Volume"] = float("nan")
    fake = FakeTicker.configured({"AAPL": frame})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 2))

    assert len(result) == 1
    assert pd.isna(result.iloc[0]["volume"])
