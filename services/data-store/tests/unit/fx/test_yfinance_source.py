import threading
import time
from datetime import date

import pandas as pd
import pytest
from data_store.fx.yfinance_source import YFinanceFxRateSource

EXPECTED_COLUMNS = ["base_currency", "quote_currency", "rate_date", "rate"]


def make_history_frame(dates, closes=None):
    """Shaped like a real Ticker.history() response for an FX pair -- a DatetimeIndex named
    'Date' and a 'Close' column (plus the OHLC/Volume columns yfinance also returns for FX
    tickers, ignored here since only Close is used as the rate)."""
    idx = pd.DatetimeIndex(dates, name="Date")
    closes = closes or [1.1 + i * 0.01 for i in range(len(dates))]
    return pd.DataFrame({"Close": closes}, index=idx)


class FakeTicker:
    """Same convention as market_data's FakeTicker -- see that module's docstring."""

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
    assert YFinanceFxRateSource().name == "yfinance"


def test_fetch_builds_ticker_from_base_and_quote(monkeypatch):
    frame = make_history_frame([date(2026, 1, 1)])
    fake = FakeTicker.configured({"EURUSD=X": frame})
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)

    YFinanceFxRateSource().fetch([("EUR", "USD")], date(2026, 1, 1), date(2026, 1, 3))

    assert fake.calls[0][0] == "EURUSD=X"


def test_fetch_reshapes_close_to_rate(monkeypatch):
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_history_frame(dates, closes=[1.08, 1.09])
    fake = FakeTicker.configured({"EURUSD=X": frame})
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)

    result = YFinanceFxRateSource().fetch([("EUR", "USD")], date(2026, 1, 1), date(2026, 1, 3))

    assert list(result.columns) == EXPECTED_COLUMNS
    assert len(result) == 2
    assert set(result["base_currency"]) == {"EUR"}
    assert set(result["quote_currency"]) == {"USD"}
    row = result[result["rate_date"] == date(2026, 1, 1)].iloc[0]
    assert row["rate"] == 1.08


def test_fetch_issues_one_call_per_pair(monkeypatch):
    pairs = [("EUR", "USD"), ("EUR", "GBP"), ("EUR", "PLN")]
    frame = make_history_frame([date(2026, 1, 1)])
    fake = FakeTicker.configured({"EURUSD=X": frame, "EURGBP=X": frame, "EURPLN=X": frame})
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)

    result = YFinanceFxRateSource().fetch(pairs, date(2026, 1, 1), date(2026, 1, 3))

    assert sorted(t for t, _ in fake.calls) == ["EURGBP=X", "EURPLN=X", "EURUSD=X"]
    assert set(result["quote_currency"]) == {"USD", "GBP", "PLN"}


def test_fetch_omits_pair_with_no_data(monkeypatch):
    fake = FakeTicker.configured(
        {"EURUSD=X": make_history_frame([date(2026, 1, 1)]), "EURXXX=X": pd.DataFrame()}
    )
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)

    result = YFinanceFxRateSource().fetch(
        [("EUR", "USD"), ("EUR", "XXX")], date(2026, 1, 1), date(2026, 1, 3)
    )

    assert set(result["quote_currency"]) == {"USD"}


def test_fetch_retries_on_transient_error_then_succeeds(monkeypatch):
    attempts = {"count": 0}
    frame = make_history_frame([date(2026, 1, 1)])

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("Connection reset by peer")
        return frame

    fake = FakeTicker.configured({"EURUSD=X": flaky})
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.fx.yfinance_source.time.sleep", lambda s: None)

    result = YFinanceFxRateSource(max_retries=3).fetch(
        [("EUR", "USD")], date(2026, 1, 1), date(2026, 1, 3)
    )

    assert attempts["count"] == 3
    assert len(result) == 1


def test_fetch_omits_pair_after_exhausting_retries_without_raising(monkeypatch, caplog):
    attempts = {"count": 0}

    def always_times_out():
        attempts["count"] += 1
        raise TimeoutError("Read timed out")

    fake = FakeTicker.configured(
        {"EURXXX=X": always_times_out, "EURUSD=X": make_history_frame([date(2026, 1, 1)])}
    )
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.fx.yfinance_source.time.sleep", lambda s: None)

    with caplog.at_level("WARNING"):
        result = YFinanceFxRateSource(max_retries=3).fetch(
            [("EUR", "XXX"), ("EUR", "USD")], date(2026, 1, 1), date(2026, 1, 3)
        )

    assert attempts["count"] == 3
    assert set(result["quote_currency"]) == {"USD"}
    assert "EURXXX=X" in caplog.text


def test_fetch_omits_pair_on_non_transient_error_without_retrying(monkeypatch):
    attempts = {"count": 0}

    def broken():
        attempts["count"] += 1
        raise ValueError("Some unexpected permanent failure")

    fake = FakeTicker.configured(
        {"EURXXX=X": broken, "EURUSD=X": make_history_frame([date(2026, 1, 1)])}
    )
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.fx.yfinance_source.time.sleep", lambda s: None)

    result = YFinanceFxRateSource(max_retries=3).fetch(
        [("EUR", "XXX"), ("EUR", "USD")], date(2026, 1, 1), date(2026, 1, 3)
    )

    assert attempts["count"] == 1
    assert set(result["quote_currency"]) == {"USD"}


def test_fetch_returns_empty_frame_with_expected_columns_when_every_pair_fails(monkeypatch):
    fake = FakeTicker.configured({"EURXXX=X": pd.DataFrame()})
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)

    result = YFinanceFxRateSource().fetch([("EUR", "XXX")], date(2026, 1, 1), date(2026, 1, 3))

    assert result.empty
    assert list(result.columns) == EXPECTED_COLUMNS


def test_fetch_raises_on_empty_pairs():
    with pytest.raises(ValueError, match="pairs"):
        YFinanceFxRateSource().fetch([], date(2026, 1, 1), date(2026, 1, 3))


def test_fetch_raises_when_start_after_end():
    with pytest.raises(ValueError, match="start_date"):
        YFinanceFxRateSource().fetch([("EUR", "USD")], date(2026, 1, 5), date(2026, 1, 1))


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

    pairs = [("EUR", f"C{i}") for i in range(10)]
    fake = FakeTicker.configured({f"EURC{i}=X": slow_history for i in range(10)})
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)

    YFinanceFxRateSource(max_workers=max_workers).fetch(pairs, date(2026, 1, 1), date(2026, 1, 3))

    assert state["peak"] <= max_workers


def test_fetch_drops_rows_with_no_close(monkeypatch):
    frame = make_history_frame([date(2026, 1, 1), date(2026, 1, 2)])
    frame.loc[pd.Timestamp(date(2026, 1, 2)), "Close"] = float("nan")
    fake = FakeTicker.configured({"EURUSD=X": frame})
    monkeypatch.setattr("data_store.fx.yfinance_source.yf.Ticker", fake)

    result = YFinanceFxRateSource().fetch([("EUR", "USD")], date(2026, 1, 1), date(2026, 1, 3))

    assert len(result) == 1
    assert result.iloc[0]["rate_date"] == date(2026, 1, 1)
