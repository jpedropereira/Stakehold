from datetime import date

import pandas as pd
import pytest
from data_store import pipeline
from data_store.config_loader import FundConfig, TickerConfig


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


def make_tracked_ticker(*, ticker, asset_type, price_source="yfinance"):
    return TickerConfig(
        ticker=ticker,
        display_name=ticker,
        asset_type=asset_type,
        price_source=price_source,
        enabled=True,
    )


class FakePriceSource:
    name = "yfinance"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append((sorted(tickers), start_date, end_date))
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


def _patch_price_source(monkeypatch, fake_source, save_calls):
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "save_prices",
        lambda conn_arg, source, asset_type, df: save_calls.append(
            (source, asset_type, sorted(df["ticker"]))
        )
        or len(df),
    )


def test_ingest_daily_ohlcv_prices_holdings_derived_tickers_as_equity(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []
    get_tickers_calls = []

    def fake_get_latest_tickers(conn_arg, fund_key):
        get_tickers_calls.append((conn_arg, fund_key))
        return ["AAPL", "MSFT"]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(conn, [fund], [], date(2026, 1, 1), date(2026, 1, 10))

    assert count == 2
    assert get_tickers_calls == [(conn, fund.key)]
    assert save_calls == [("yfinance", "equity", ["AAPL", "MSFT"])]


def test_ingest_daily_ohlcv_prices_tracked_tickers_with_their_own_asset_type(monkeypatch):
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]

    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(conn, [], tracked, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 1
    assert save_calls == [("yfinance", "etf", ["IWDA.AS"])]


def test_ingest_daily_ohlcv_dedupes_ticker_in_both_holdings_and_tracked(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    # BABA is both a fund holding and an explicitly tracked ticker (both asset_type=equity
    # here, so they land in the same group) -- must be fetched once, not twice.
    monkeypatch.setattr(pipeline, "get_latest_tickers", lambda c, k: ["BABA", "AAPL"])
    tracked = [make_tracked_ticker(ticker="BABA", asset_type="equity")]
    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(conn, [fund], tracked, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 2
    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["AAPL", "BABA"]


def test_ingest_daily_ohlcv_tracked_ticker_asset_type_wins_over_holdings_default(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    # A ticker that appears in fund holdings (which would default it to equity) but is
    # explicitly configured with a different asset_type in tracked_tickers -- the explicit
    # config wins.
    monkeypatch.setattr(pipeline, "get_latest_tickers", lambda c, k: ["WEIRD"])
    tracked = [make_tracked_ticker(ticker="WEIRD", asset_type="etf")]
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(conn, [fund], tracked, date(2026, 1, 1), date(2026, 1, 10))

    assert save_calls == [("yfinance", "etf", ["WEIRD"])]


def test_ingest_daily_ohlcv_groups_by_asset_type_for_separate_save_calls(monkeypatch):
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []
    tracked = [
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
        make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
    ]

    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(conn, [], tracked, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 2
    assert sorted(save_calls) == sorted(
        [("yfinance", "etf", ["IWDA.AS"]), ("yfinance", "crypto", ["BTC-USD"])]
    )
    assert len(fake_source.fetch_calls) == 2


def test_ingest_daily_ohlcv_dedupes_across_multiple_funds(monkeypatch):
    fund_a = make_fund(key="fund_a")
    fund_b = make_fund(key="fund_b")
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    def fake_get_latest_tickers(conn_arg, fund_key):
        return {"fund_a": ["AAPL", "MSFT"], "fund_b": ["AAPL", "NVDA"]}[fund_key]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(conn, [fund_a, fund_b], [], date(2026, 1, 1), date(2026, 1, 10))

    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["AAPL", "MSFT", "NVDA"]


def test_ingest_daily_ohlcv_strips_and_dedupes_raw_holdings_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [" AAPL", "AAPL", "MSFT ", "", None],
    )
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(conn, [fund], [], date(2026, 1, 1), date(2026, 1, 10))

    assert fake_source.fetch_calls[0][0] == ["AAPL", "MSFT"]


def test_ingest_daily_ohlcv_returns_zero_when_no_tickers():
    count = pipeline.ingest_daily_ohlcv(object(), [], [], date(2026, 1, 1), date(2026, 1, 10))

    assert count == 0


def test_ingest_daily_ohlcv_returns_zero_when_price_source_has_no_data(monkeypatch):
    conn = object()

    class EmptyPriceSource:
        name = "empty_source"

        def fetch(self, tickers, start_date, end_date):
            return pd.DataFrame()

    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: EmptyPriceSource())
    save_calls = []
    monkeypatch.setattr(pipeline, "save_prices", lambda *a: save_calls.append(a) or len(a[-1]))

    count = pipeline.ingest_daily_ohlcv(conn, [], tracked, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 0
    assert save_calls == []


def test_ingest_daily_ohlcv_raises_on_unknown_price_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.ingest_daily_ohlcv(object(), [], tracked, date(2026, 1, 1), date(2026, 1, 10))


class FakeSinglePriceSource:
    name = "yfinance"

    def __init__(self, *, fail_tickers=None):
        self.fetch_calls = []
        self.fail_tickers = fail_tickers or set()

    def fetch(self, tickers, start_date, end_date):
        ticker = tickers[0]
        self.fetch_calls.append((ticker, start_date, end_date))
        if ticker in self.fail_tickers:
            raise RuntimeError(f"{ticker} failed")
        return pd.DataFrame({"ticker": [ticker], "price_date": [start_date]})


def test_backfill_fetches_full_range_when_no_existing_data(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("AAPL", date(1900, 1, 1), date(2026, 1, 1))]


def test_backfill_fetches_only_gap_before_existing_floor(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_price_dates",
        lambda conn, source, tickers: {"AAPL": date(2010, 1, 1)},
    )
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    # fetch()'s end is exclusive, so passing the floor itself already stops the day before
    # it -- no -1 day adjustment (that would silently drop the trading day right before it).
    assert fake_source.fetch_calls == [("AAPL", date(1900, 1, 1), date(2010, 1, 1))]


def test_backfill_skips_ticker_already_covered_to_earliest_date(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_price_dates",
        lambda conn, source, tickers: {"AAPL": date(1900, 1, 1)},
    )
    save_calls = []
    monkeypatch.setattr(pipeline, "save_prices", lambda *a: save_calls.append(a))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    count = pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == []
    assert save_calls == []
    assert count == 0


def test_backfill_sleeps_after_fetched_and_failed_tickers_not_after_skips(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource(fail_tickers={"BAD"})
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="BAD", asset_type="equity"),
        make_tracked_ticker(ticker="COVERED", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_price_dates",
        lambda conn, source, tickers: {"COVERED": date(1900, 1, 1)},
    )
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    sleep_calls = []
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: sleep_calls.append(s))

    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=5
    )

    # AAPL (fetched successfully) and BAD (failed) both sleep; COVERED (skipped) does not.
    assert sleep_calls == [5, 5]


def test_backfill_continues_after_one_ticker_fails(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource(fail_tickers={"BAD"})
    tracked = [
        make_tracked_ticker(ticker="BAD", asset_type="equity"),
        make_tracked_ticker(ticker="GOOD", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    save_calls = []
    monkeypatch.setattr(
        pipeline,
        "save_prices",
        lambda conn, source, asset_type, df: save_calls.append(df) or len(df),
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    count = pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert count == 1  # only GOOD saved
    assert len(save_calls) == 1


def test_backfill_groups_by_source_and_type_and_queries_floors_per_group(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
        make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
    ]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    floor_calls = []

    def fake_floors(conn, source, tickers):
        floor_calls.append((source, sorted(tickers)))
        return {}

    monkeypatch.setattr(pipeline, "get_earliest_price_dates", fake_floors)
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert sorted(floor_calls) == sorted([("yfinance", ["BTC-USD"]), ("yfinance", ["IWDA.AS"])])


def test_backfill_raises_on_unknown_price_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.backfill_historical_ohlcv(
            object(), [], tracked, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
        )


def test_backfill_returns_zero_when_no_tickers():
    count = pipeline.backfill_historical_ohlcv(
        object(), [], [], date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert count == 0
