import logging
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
    test -- the class-level responses/calls would otherwise leak between tests.

    .history_metadata is what yfinance fills in from the same response: `currencies` maps a
    ticker to the quote currency it reports (None for metadata with no currency). A ticker not
    in `currencies` reports USD, so tests that don't care about units get a major currency."""

    responses: dict = {}
    currencies: dict = {}
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

    @property
    def history_metadata(self):
        currency = type(self).currencies.get(self.ticker, "USD")
        return {} if currency is None else {"currency": currency}

    @classmethod
    def configured(cls, responses, currencies=None):
        return type(
            "ConfiguredFakeTicker",
            (cls,),
            {"responses": responses, "currencies": currencies or {}, "calls": []},
        )


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


def test_fetch_drops_an_unfinished_bar_with_no_close(monkeypatch):
    # Seen on 2026-09-03: a run shortly after the US close got open/high/low and a partial
    # volume but no close for 918 tickers. Stored, the row was never fetched again, and
    # returns/calculator.py nulled the next day's return for want of a prior close.
    frame = make_history_frame([date(2026, 1, 1), date(2026, 1, 2)])
    frame.loc[pd.Timestamp(date(2026, 1, 2)), ["Close", "Adj Close"]] = float("nan")
    fake = FakeTicker.configured({"AAPL": frame})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 3))

    assert result["price_date"].tolist() == [date(2026, 1, 1)]


def test_fetch_keeps_rows_missing_only_volume(monkeypatch):
    frame = make_history_frame([date(2026, 1, 1)])
    frame.loc[pd.Timestamp(date(2026, 1, 1)), "Volume"] = float("nan")
    fake = FakeTicker.configured({"AAPL": frame})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    result = YFinanceMarketDataSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 2))

    assert len(result) == 1
    assert pd.isna(result.iloc[0]["volume"])


class FakeHttpError(OSError):
    """Shaped like curl_cffi's HTTPError: an OSError carrying the response."""

    def __init__(self, status_code):
        super().__init__(f"HTTP Error {status_code}: ")
        self.response = type("Response", (), {"status_code": status_code})()


def test_fetch_reports_a_ticker_it_couldnt_reach_the_provider_for(monkeypatch):
    # Without this, a ticker that failed every retry on a network error looks exactly like one
    # the provider has no data for: both are simply missing from the frame.
    def unreachable():
        raise OSError("Failed to perform, curl: (6) Could not resolve host: query2.yahoo.com")

    fake = FakeTicker.configured(
        {
            "OFFLINE": unreachable,
            "EMPTY": pd.DataFrame(),
            "GOOD": make_history_frame([date(2026, 1, 1)]),
        }
    )
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.market_data.yfinance_source.time.sleep", lambda s: None)
    source = YFinanceMarketDataSource(max_retries=2)

    result = source.fetch(["OFFLINE", "EMPTY", "GOOD"], date(2026, 1, 1), date(2026, 1, 3))

    assert set(result["ticker"]) == {"GOOD"}
    assert source.unreachable == {"OFFLINE"}


def test_fetch_doesnt_count_an_unknown_symbol_as_unreachable(monkeypatch):
    fake = FakeTicker.configured({"GHOST": FakeHttpError(404)})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.market_data.yfinance_source.time.sleep", lambda s: None)
    source = YFinanceMarketDataSource(max_retries=2)

    source.fetch(["GHOST"], date(2026, 1, 1), date(2026, 1, 3))

    assert source.unreachable == set()


def test_each_fetch_reports_only_its_own_unreachable_tickers(monkeypatch):
    def unreachable():
        raise TimeoutError("timed out")

    fake = FakeTicker.configured(
        {"OFFLINE": unreachable, "GOOD": make_history_frame([date(2026, 1, 1)])}
    )
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)
    monkeypatch.setattr("data_store.market_data.yfinance_source.time.sleep", lambda s: None)
    source = YFinanceMarketDataSource(max_retries=1)

    source.fetch(["OFFLINE"], date(2026, 1, 1), date(2026, 1, 3))
    source.fetch(["GOOD"], date(2026, 1, 1), date(2026, 1, 3))

    assert source.unreachable == set()


# --- Minor currency units ----------------------------------------------------------------------

PRICE_COLUMNS = ["open", "high", "low", "close", "adj_close"]


@pytest.mark.parametrize("minor_unit", ["GBp", "ZAc", "ILA"])
def test_fetch_rescales_a_minor_unit_quote_to_its_major_unit(monkeypatch, minor_unit):
    day = date(2026, 1, 1)
    frame = make_history_frame([day], dividends={day: 12.5}, stock_splits={day: 2.0})
    fake = FakeTicker.configured({"VOD.L": frame}, currencies={"VOD.L": minor_unit})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    row = YFinanceMarketDataSource().fetch(["VOD.L"], day, date(2026, 1, 2)).iloc[0]

    assert [row[c] for c in PRICE_COLUMNS] == pytest.approx([1.0, 1.01, 0.99, 1.005, 1.005])
    assert row["dividends"] == pytest.approx(0.125)
    # Neither is an amount of money: a share count and a ratio stay as they are.
    assert row["volume"] == 1000.0
    assert row["stock_splits"] == 2.0


@pytest.mark.parametrize("currency", ["USD", "GBP", "ZAR", "ILS"])
def test_fetch_leaves_a_major_currency_quote_alone(monkeypatch, currency):
    day = date(2026, 1, 1)
    frame = make_history_frame([day], dividends={day: 12.5})
    fake = FakeTicker.configured({"X": frame}, currencies={"X": currency})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    row = YFinanceMarketDataSource().fetch(["X"], day, date(2026, 1, 2)).iloc[0]

    assert [row[c] for c in PRICE_COLUMNS] == [100.0, 101.0, 99.0, 100.5, 100.5]
    assert row["dividends"] == 12.5


def test_fetch_keeps_and_warns_about_a_quote_with_no_currency(monkeypatch, caplog):
    day = date(2026, 1, 1)
    fake = FakeTicker.configured({"VOD.L": make_history_frame([day])}, currencies={"VOD.L": None})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    row = YFinanceMarketDataSource().fetch(["VOD.L"], day, date(2026, 1, 2)).iloc[0]

    assert row["close"] == 100.5
    assert "VOD.L" in caplog.text
    assert "no currency" in caplog.text


def test_fetch_summarises_the_minor_unit_quotes_it_rescaled(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    day = date(2026, 1, 1)
    frame = make_history_frame([day])
    fake = FakeTicker.configured(
        {"VOD.L": frame, "BARC.L": frame, "NPN.JO": frame, "AAPL": frame},
        currencies={"VOD.L": "GBp", "BARC.L": "GBp", "NPN.JO": "ZAc"},
    )
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    YFinanceMarketDataSource().fetch(["VOD.L", "BARC.L", "NPN.JO", "AAPL"], day, date(2026, 1, 2))

    assert "rescaled 3 of 4 ticker(s) quoted in minor units to the major unit" in caplog.text
    assert "GBp: 2" in caplog.text
    assert "ZAc: 1" in caplog.text


def test_fetch_logs_nothing_about_minor_units_when_there_were_none(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    day = date(2026, 1, 1)
    fake = FakeTicker.configured({"AAPL": make_history_frame([day])})
    monkeypatch.setattr("data_store.market_data.yfinance_source.yf.Ticker", fake)

    YFinanceMarketDataSource().fetch(["AAPL"], day, date(2026, 1, 2))

    assert "minor unit" not in caplog.text
