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


def make_sparse_yf_frame(tickers, dates, populated):
    """A frame shaped like a real multi-ticker `yf.download`: the index is the *union* of every
    ticker's trading days, so a ticker that didn't trade on a given date has NaN across every
    price field. `populated` is the set of (date, ticker) pairs that actually have data."""
    idx = pd.DatetimeIndex(dates, name="Date")
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Adj Close", "Volume"], tickers],
        names=["Price", "Ticker"],
    )
    frame = pd.DataFrame(float("nan"), index=idx, columns=cols)
    for i, (d, ticker) in enumerate(sorted(populated)):
        for field in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
            frame.loc[pd.Timestamp(d), (field, ticker)] = float(i + 1)
    return frame


def test_fetch_drops_rows_where_the_ticker_did_not_trade(monkeypatch):
    # AAPL trades both days; 0700.HK only on the 2nd (the 1st is a Hong Kong holiday). The
    # union index means yfinance hands back an all-NaN row for (2026-01-01, 0700.HK) -- it must
    # not reach the caller as if it were a real observation.
    tickers = ["AAPL", "0700.HK"]
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_sparse_yf_frame(
        tickers,
        dates,
        populated={
            (date(2026, 1, 1), "AAPL"),
            (date(2026, 1, 2), "AAPL"),
            (date(2026, 1, 2), "0700.HK"),
        },
    )

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinancePriceSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    pairs = {(row["ticker"], row["price_date"]) for _, row in result.iterrows()}
    assert pairs == {
        ("AAPL", date(2026, 1, 1)),
        ("AAPL", date(2026, 1, 2)),
        ("0700.HK", date(2026, 1, 2)),
    }


def test_fetch_drops_every_row_for_a_ticker_with_no_data_at_all(monkeypatch):
    # A ticker yfinance knows nothing about (wrong suffix, delisted) comes back as all-NaN for
    # every date in the batch. Writing those would make an unpriceable ticker look priced --
    # exactly the silent-zero-rows failure the exchange-suffix resolution exists to prevent.
    tickers = ["AAPL", "BOGUS.XX"]
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    frame = make_sparse_yf_frame(
        tickers,
        dates,
        populated={(date(2026, 1, 1), "AAPL"), (date(2026, 1, 2), "AAPL")},
    )

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinancePriceSource().fetch(tickers, date(2026, 1, 1), date(2026, 1, 3))

    assert set(result["ticker"]) == {"AAPL"}
    assert len(result) == 2


def test_fetch_keeps_rows_that_are_only_partially_populated(monkeypatch):
    # Not every NaN is junk: a real row with a missing volume (or adj_close) is still a real
    # observation. Only rows with no OHLC at all are dropped.
    frame = make_sparse_yf_frame(
        ["AAPL"], [date(2026, 1, 1)], populated={(date(2026, 1, 1), "AAPL")}
    )
    frame.loc[pd.Timestamp(date(2026, 1, 1)), ("Volume", "AAPL")] = float("nan")

    monkeypatch.setattr("data_store.prices.yfinance_source.yf.download", lambda *a, **k: frame)

    result = YFinancePriceSource().fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 2))

    assert len(result) == 1
    assert pd.isna(result.iloc[0]["volume"])
