"""The daily ingests' catch-up after an outage: ingest_market_data and ingest_fx_rates with a
catch_up_limit.

A fixed rolling window loses data for good when the DAG doesn't run for longer than the window:
the DAG didn't run from 2026-09-04 to 2026-09-22, the next run fetched only the 7 days before it,
and 2026-09-04..09-14 was never stored for any ticker or currency pair. backfill can't repair
that hole either, since it only fetches below a ticker's earliest stored date.
"""

import logging
from datetime import date

import pandas as pd
from data_store import pipeline
from data_store.config_loader import CurrenciesConfig, CurrencyConfig, TickerConfig

START = date(2026, 9, 15)
END = date(2026, 9, 22)
LIMIT = date(2025, 9, 21)


def make_tracked_ticker(ticker, asset_type="equity"):
    return TickerConfig(
        ticker=ticker,
        display_name=ticker,
        asset_type=asset_type,
        price_source="yfinance",
        enabled=True,
    )


class RecordingMarketDataSource:
    name = "yfinance"
    unreachable = frozenset()

    def __init__(self, *, splits_on=None, fail_from=None):
        self.splits_on = splits_on or {}
        self.fail_from = fail_from
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append((sorted(tickers), start_date, end_date))
        if start_date == self.fail_from:
            raise RuntimeError("fetch exploded")
        return pd.DataFrame(
            {
                "ticker": tickers,
                "price_date": [start_date] * len(tickers),
                "stock_splits": [self.splits_on.get(t, 0.0) for t in tickers],
            }
        )


def _ingest_market_data(
    monkeypatch, tickers, latest, *, source=None, catch_up_limit=LIMIT, floors=None
):
    source = source or RecordingMarketDataSource()
    lookups = []

    def fake_latest(conn, source_name, asked):
        lookups.append((source_name, sorted(asked)))
        return {t: d for t, d in latest.items() if t in asked}

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda c, s, a, df: len(df))
    monkeypatch.setattr(pipeline, "get_latest_close_dates", fake_latest)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda c, s, t: floors or {})
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    result = pipeline.ingest_market_data(
        object(),
        [],
        [make_tracked_ticker(t) for t in tickers],
        {},
        START,
        END,
        catch_up_limit=catch_up_limit,
    )
    return result, source.fetch_calls, lookups


# --- ingest_market_data -------------------------------------------------------------------------


def test_a_ticker_that_fell_behind_is_fetched_from_its_last_stored_close(monkeypatch):
    result, fetches, _ = _ingest_market_data(
        monkeypatch, ["AAPL", "MSFT"], {"AAPL": date(2026, 9, 2), "MSFT": date(2026, 9, 19)}
    )

    assert fetches == [
        (["AAPL"], date(2026, 9, 2), END),
        (["MSFT"], START, END),
    ]
    assert result["rows_saved"] == 2
    assert result["status"] == "ok"


def test_a_ticker_with_nothing_stored_uses_the_normal_window(monkeypatch):
    _, fetches, _ = _ingest_market_data(monkeypatch, ["NEW"], {})

    assert fetches == [(["NEW"], START, END)]


def test_tickers_that_fell_behind_to_the_same_day_share_one_fetch(monkeypatch):
    _, fetches, _ = _ingest_market_data(
        monkeypatch, ["AAPL", "MSFT"], {"AAPL": date(2026, 9, 2), "MSFT": date(2026, 9, 2)}
    )

    assert fetches == [(["AAPL", "MSFT"], date(2026, 9, 2), END)]


def test_the_lookup_asks_for_each_groups_tickers_under_its_source(monkeypatch):
    _, _, lookups = _ingest_market_data(monkeypatch, ["AAPL", "MSFT"], {})

    assert lookups == [("yfinance", ["AAPL", "MSFT"])]


def test_catch_up_stops_at_the_limit_and_warns_about_the_rest(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    _, fetches, _ = _ingest_market_data(monkeypatch, ["OLD"], {"OLD": date(2024, 1, 5)})

    assert fetches == [(["OLD"], LIMIT, END)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("OLD" in m and "2024-01-05" in m for m in warnings)


def test_catching_up_is_logged_with_how_far_back_it_reaches(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    _ingest_market_data(
        monkeypatch, ["AAPL", "MSFT"], {"AAPL": date(2026, 9, 2), "MSFT": date(2026, 9, 19)}
    )

    assert "1 ticker(s) behind" in caplog.text
    assert "2026-09-02" in caplog.text


def test_without_a_catch_up_limit_the_window_is_fixed_and_nothing_is_looked_up(monkeypatch):
    def no_lookup(*args):
        raise AssertionError("looked up latest dates without a catch_up_limit")

    source = RecordingMarketDataSource()
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda c, s, a, df: len(df))
    monkeypatch.setattr(pipeline, "get_latest_close_dates", no_lookup)

    pipeline.ingest_market_data(object(), [], [make_tracked_ticker("AAPL")], {}, START, END)

    assert source.fetch_calls == [(["AAPL"], START, END)]


def test_a_failed_catch_up_fetch_does_not_cost_the_normal_window(monkeypatch):
    source = RecordingMarketDataSource(fail_from=date(2026, 9, 2))

    result, fetches, _ = _ingest_market_data(
        monkeypatch,
        ["AAPL", "MSFT"],
        {"AAPL": date(2026, 9, 2), "MSFT": date(2026, 9, 19)},
        source=source,
    )

    assert (["MSFT"], START, END) in fetches
    assert result["rows_saved"] == 1
    assert result["status"] == "partial"


def test_a_split_refresh_is_skipped_when_the_catch_up_already_covered_all_history(monkeypatch):
    # AAPL's whole stored history starts on its last close, so the catch-up fetch restated all
    # of it; re-fetching it again for the split would be wasted.
    source = RecordingMarketDataSource(splits_on={"AAPL": 4.0})

    _, fetches, _ = _ingest_market_data(
        monkeypatch,
        ["AAPL"],
        {"AAPL": date(2026, 9, 2)},
        source=source,
        floors={"AAPL": date(2026, 9, 2)},
    )

    assert fetches == [(["AAPL"], date(2026, 9, 2), END)]


def test_a_split_refresh_still_restates_history_older_than_the_catch_up(monkeypatch):
    source = RecordingMarketDataSource(splits_on={"AAPL": 4.0})

    _, fetches, _ = _ingest_market_data(
        monkeypatch,
        ["AAPL"],
        {"AAPL": date(2026, 9, 2)},
        source=source,
        floors={"AAPL": date(2020, 1, 2)},
    )

    assert fetches == [
        (["AAPL"], date(2026, 9, 2), END),
        (["AAPL"], date(2020, 1, 2), END),
    ]


# --- ingest_fx_rates ----------------------------------------------------------------------------


def make_currencies(codes=("EUR", "USD", "GBP")):
    return CurrenciesConfig(
        base_currency="EUR",
        currencies=[CurrencyConfig(code=c, enabled=True) for c in codes],
    )


class RecordingFxRateSource:
    name = "yfinance"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, pairs, start_date, end_date):
        self.fetch_calls.append((sorted(pairs), start_date, end_date))
        return pd.DataFrame(
            {
                "base_currency": [b for b, _ in pairs],
                "quote_currency": [q for _, q in pairs],
                "rate_date": [start_date] * len(pairs),
                "rate": [1.1] * len(pairs),
            }
        )


def _ingest_fx_rates(monkeypatch, latest, *, catch_up_limit=LIMIT):
    source = RecordingFxRateSource()
    lookups = []

    def fake_latest(conn, source_name, asked):
        lookups.append((source_name, sorted(asked)))
        return {p: d for p, d in latest.items() if p in asked}

    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr(pipeline, "save_fx_rates", lambda c, s, df: len(df))
    monkeypatch.setattr(pipeline, "get_latest_fx_dates", fake_latest)

    result = pipeline.ingest_fx_rates(
        object(), make_currencies(), START, END, catch_up_limit=catch_up_limit
    )
    return result, source.fetch_calls, lookups


def test_a_pair_that_fell_behind_is_fetched_from_its_last_stored_rate(monkeypatch):
    result, fetches, _ = _ingest_fx_rates(
        monkeypatch, {("EUR", "USD"): date(2026, 9, 3), ("EUR", "GBP"): date(2026, 9, 19)}
    )

    assert fetches == [
        ([("EUR", "USD")], date(2026, 9, 3), END),
        ([("EUR", "GBP")], START, END),
    ]
    assert result["rows_saved"] == 2


def test_a_pair_with_nothing_stored_uses_the_normal_window(monkeypatch):
    _, fetches, lookups = _ingest_fx_rates(monkeypatch, {})

    assert fetches == [([("EUR", "GBP"), ("EUR", "USD")], START, END)]
    assert lookups == [("yfinance", [("EUR", "GBP"), ("EUR", "USD")])]


def test_fx_catch_up_stops_at_the_limit_and_warns(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    _, fetches, _ = _ingest_fx_rates(
        monkeypatch, {("EUR", "USD"): date(2024, 1, 5), ("EUR", "GBP"): date(2026, 9, 19)}
    )

    assert ([("EUR", "USD")], LIMIT, END) in fetches
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("EUR/USD" in m and "2024-01-05" in m for m in warnings)


def test_fx_without_a_catch_up_limit_the_window_is_fixed(monkeypatch):
    def no_lookup(*args):
        raise AssertionError("looked up latest dates without a catch_up_limit")

    source = RecordingFxRateSource()
    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr(pipeline, "save_fx_rates", lambda c, s, df: len(df))
    monkeypatch.setattr(pipeline, "get_latest_fx_dates", no_lookup)

    pipeline.ingest_fx_rates(object(), make_currencies(), START, END)

    assert source.fetch_calls == [([("EUR", "GBP"), ("EUR", "USD")], START, END)]
