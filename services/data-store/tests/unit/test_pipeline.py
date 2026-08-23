import logging
import pickle
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


class FakeMarketDataSource:
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


def _patch_market_data_source(monkeypatch, fake_source, save_calls):
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "save_raw_market_data",
        lambda conn_arg, source, asset_type, df: save_calls.append(
            (source, asset_type, sorted(df["ticker"]))
        )
        or len(df),
    )


# --- ingest_market_data -----------------------------------------------------------------------


def test_ingest_market_data_prices_holdings_derived_tickers_as_equity(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []
    get_tickers_calls = []

    def fake_get_latest_tickers(conn_arg, fund_key):
        get_tickers_calls.append((conn_arg, fund_key))
        return [("AAPL", "NASDAQ"), ("MSFT", "NASDAQ")]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    result = pipeline.ingest_market_data(
        conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert result["rows_saved"] == 2
    assert get_tickers_calls == [(conn, fund.key)]
    assert save_calls == [("yfinance", "equity", ["AAPL", "MSFT"])]


def test_ingest_market_data_suffixes_holdings_derived_ticker_by_exchange(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("0700", "Hong Kong Exchanges And Clearing Ltd")],
    )
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(
        conn,
        [fund],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    assert save_calls == [("yfinance", "equity", ["0700.HK"])]


def test_ingest_market_data_skips_and_warns_on_unmapped_exchange(monkeypatch, caplog):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("AAPL", "NASDAQ"), ("0005", "NO MARKET (E.G. UNLISTED)")],
    )
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    with caplog.at_level("WARNING"):
        result = pipeline.ingest_market_data(
            conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
        )

    assert result["rows_saved"] == 1
    assert save_calls == [("yfinance", "equity", ["AAPL"])]
    assert "0005" in caplog.text
    assert "NO MARKET (E.G. UNLISTED)" in caplog.text


def test_ingest_market_data_prices_tracked_tickers_with_their_own_asset_type(monkeypatch):
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]

    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    result = pipeline.ingest_market_data(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert result["rows_saved"] == 1
    assert save_calls == [("yfinance", "etf", ["IWDA.AS"])]


def test_ingest_market_data_tracked_ticker_never_suffix_resolved(monkeypatch):
    # tracked_tickers entries are already fully yfinance-ready -- exchange_suffixes having no
    # (or any) entry for them must never matter, since they never go through resolution at all.
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]

    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    result = pipeline.ingest_market_data(
        conn, [], tracked, {"Euronext Amsterdam": ".AS"}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert result["rows_saved"] == 1
    assert save_calls == [("yfinance", "etf", ["IWDA.AS"])]


def test_ingest_market_data_dedupes_ticker_in_both_holdings_and_tracked(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    # BABA is both a fund holding and an explicitly tracked ticker (both asset_type=equity
    # here, so they land in the same group) -- must be fetched once, not twice.
    monkeypatch.setattr(
        pipeline, "get_latest_tickers", lambda c, k: [("BABA", "NASDAQ"), ("AAPL", "NASDAQ")]
    )
    tracked = [make_tracked_ticker(ticker="BABA", asset_type="equity")]
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    result = pipeline.ingest_market_data(
        conn, [fund], tracked, {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert result["rows_saved"] == 2
    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["AAPL", "BABA"]


def test_ingest_market_data_tracked_ticker_asset_type_wins_over_holdings_default(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    # A ticker that appears in fund holdings (which would default it to equity) but is
    # explicitly configured with a different asset_type in tracked_tickers -- the explicit
    # config wins.
    monkeypatch.setattr(pipeline, "get_latest_tickers", lambda c, k: [("WEIRD", "NASDAQ")])
    tracked = [make_tracked_ticker(ticker="WEIRD", asset_type="etf")]
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(
        conn, [fund], tracked, {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert save_calls == [("yfinance", "etf", ["WEIRD"])]


def test_ingest_market_data_groups_by_asset_type_for_separate_save_calls(monkeypatch):
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []
    tracked = [
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
        make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
    ]

    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    result = pipeline.ingest_market_data(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert result["rows_saved"] == 2
    assert sorted(save_calls) == sorted(
        [("yfinance", "etf", ["IWDA.AS"]), ("yfinance", "crypto", ["BTC-USD"])]
    )
    assert len(fake_source.fetch_calls) == 2


def test_ingest_market_data_dedupes_across_multiple_funds(monkeypatch):
    fund_a = make_fund(key="fund_a")
    fund_b = make_fund(key="fund_b")
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    def fake_get_latest_tickers(conn_arg, fund_key):
        return {
            "fund_a": [("AAPL", "NASDAQ"), ("MSFT", "NASDAQ")],
            "fund_b": [("AAPL", "NASDAQ"), ("NVDA", "NASDAQ")],
        }[fund_key]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(
        conn, [fund_a, fund_b], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["AAPL", "MSFT", "NVDA"]


def test_ingest_market_data_dedupes_on_resolved_suffixed_ticker(monkeypatch):
    fund_a = make_fund(key="fund_a")
    fund_b = make_fund(key="fund_b")
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    # Same raw ticker + exchange combo held by two funds -- must resolve to the same suffixed
    # ticker and be fetched once, proving dedup happens post-suffix, not pre-suffix.
    def fake_get_latest_tickers(conn_arg, fund_key):
        return {
            "fund_a": [("0700", "Hong Kong Exchanges And Clearing Ltd")],
            "fund_b": [("0700", "Hong Kong Exchanges And Clearing Ltd")],
        }[fund_key]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(
        conn,
        [fund_a, fund_b],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["0700.HK"]


def test_ingest_market_data_strips_and_dedupes_raw_holdings_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [
            (" AAPL", "NASDAQ"),
            ("AAPL", "NASDAQ"),
            ("MSFT ", "NASDAQ"),
            ("", "NASDAQ"),
            (None, "NASDAQ"),
        ],
    )
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(
        conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert fake_source.fetch_calls[0][0] == ["AAPL", "MSFT"]


def test_ingest_market_data_returns_zero_when_no_tickers():
    result = pipeline.ingest_market_data(object(), [], [], {}, date(2026, 1, 1), date(2026, 1, 10))

    assert result["rows_saved"] == 0


def test_ingest_market_data_returns_zero_when_source_has_no_data(monkeypatch):
    conn = object()

    class EmptyMarketDataSource:
        name = "empty_source"

        def fetch(self, tickers, start_date, end_date):
            return pd.DataFrame()

    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: EmptyMarketDataSource())
    save_calls = []
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda *a: save_calls.append(a) or len(a[-1])
    )

    result = pipeline.ingest_market_data(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert result["rows_saved"] == 0
    assert save_calls == []


def test_ingest_market_data_raises_on_unknown_market_data_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.ingest_market_data(object(), [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))


def test_ingest_market_data_filters_to_only_tickers_when_given(monkeypatch):
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="MSFT", asset_type="equity"),
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
    ]

    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    result = pipeline.ingest_market_data(
        conn,
        [],
        tracked,
        {},
        date(2026, 1, 1),
        date(2026, 1, 10),
        only_tickers={"MSFT"},
    )

    assert result["rows_saved"] == 1
    assert save_calls == [("yfinance", "equity", ["MSFT"])]


def test_ingest_market_data_only_tickers_can_target_holdings_derived_suffixed_tickers(
    monkeypatch,
):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [
            ("0700", "Hong Kong Exchanges And Clearing Ltd"),
            ("AAPL", "NASDAQ"),
        ],
    )
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(
        conn,
        [fund],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK", "NASDAQ": None},
        date(2026, 1, 1),
        date(2026, 1, 10),
        only_tickers={"0700.HK"},
    )

    assert save_calls == [("yfinance", "equity", ["0700.HK"])]


def test_ingest_market_data_applies_ticker_overrides_for_holdings_derived_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("BRKB", "New York Stock Exchange Inc.")],
    )
    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(
        conn,
        [fund],
        [],
        {"New York Stock Exchange Inc.": None},
        date(2026, 1, 1),
        date(2026, 1, 10),
        ticker_overrides={"BRKB": "BRK-B"},
    )

    assert save_calls == [("yfinance", "equity", ["BRK-B"])]


def test_ingest_market_data_logs_run_summary(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    conn = object()
    fake_source = FakeMarketDataSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    _patch_market_data_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_market_data(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    out = caplog.text
    assert "1" in out  # resolved ticker count appears somewhere in the summary
    assert "AAPL" not in out  # summary is count-based, not a per-ticker dump


def test_ingest_market_data_logs_no_data_distinctly(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    conn = object()

    class EmptyMarketDataSource:
        name = "empty_source"

        def fetch(self, tickers, start_date, end_date):
            return pd.DataFrame()

    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: EmptyMarketDataSource())
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda *a: len(a[-1]))

    pipeline.ingest_market_data(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert "no data" in caplog.text.lower()


class FlakyMarketDataSource:
    name = "yfinance"

    def __init__(self, *, fail_asset_types):
        self.fail_asset_types = fail_asset_types
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append(sorted(tickers))
        if any(t.startswith("FAIL") for t in tickers):
            raise RuntimeError("yfinance exploded")
        return pd.DataFrame({"ticker": tickers, "price_date": [start_date] * len(tickers)})


def test_ingest_market_data_continues_after_one_group_fails(monkeypatch, caplog):
    conn = object()
    fake_source = FlakyMarketDataSource(fail_asset_types={"etf"})
    save_calls = []
    tracked = [
        make_tracked_ticker(ticker="FAIL.AS", asset_type="etf"),
        make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
    ]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "save_raw_market_data",
        lambda conn_arg, source, asset_type, df: save_calls.append(asset_type) or len(df),
    )

    with caplog.at_level(logging.WARNING):
        result = pipeline.ingest_market_data(
            conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10)
        )

    assert save_calls == ["crypto"]  # the healthy group still landed
    assert result["rows_saved"] == 1
    assert "FAIL.AS" in caplog.text or "etf" in caplog.text


def test_ingest_market_data_reports_failed_groups_in_the_summary(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    conn = object()
    fake_source = FlakyMarketDataSource(fail_asset_types={"etf"})
    tracked = [
        make_tracked_ticker(ticker="FAIL.AS", asset_type="etf"),
        make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
    ]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda conn_arg, source, at, df: len(df))

    pipeline.ingest_market_data(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    # A partial run must not read as a clean one.
    assert "1 group(s) failed" in caplog.text


def test_a_failed_group_logs_its_traceback(monkeypatch, caplog):
    # Without exc_info the log says a group failed but not where, and these run unattended
    # where the log is all you get.
    caplog.set_level(logging.WARNING)
    fake_source = FlakyMarketDataSource(fail_asset_types={"etf"})
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda conn, source, at, df: len(df))

    pipeline.ingest_market_data(
        object(),
        [],
        [make_tracked_ticker(ticker="FAIL.AS", asset_type="etf")],
        {},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    record = next(r for r in caplog.records if "failed" in r.message)
    assert record.exc_info is not None, "traceback was dropped"


def test_ingest_market_data_warns_when_a_requested_ticker_matches_nothing(monkeypatch, caplog):
    # A typo'd --ticker and a genuinely empty universe both end with zero tickers; they must
    # not look the same in the output.
    conn = object()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]
    _patch_market_data_source(monkeypatch, FakeMarketDataSource(), [])

    with caplog.at_level(logging.WARNING):
        pipeline.ingest_market_data(
            conn,
            [],
            tracked,
            {},
            date(2026, 1, 1),
            date(2026, 1, 10),
            only_tickers={"AAPL", "TYPO", "ALSO-WRONG"},
        )

    assert "TYPO" in caplog.text
    assert "ALSO-WRONG" in caplog.text
    assert "AAPL" not in caplog.text  # the one that did match isn't an anomaly


def test_requesting_a_ticker_that_matches_nothing_is_a_failure_not_a_quiet_success(monkeypatch):
    # Asking for a specific ticker and getting nothing is not "there was nothing to do" -- the
    # run did not do the thing it was asked to. Reported as nothing_to_do it would show green
    # in Airflow, which is precisely the silent success this contract exists to prevent.
    _patch_market_data_source(monkeypatch, FakeMarketDataSource(), [])
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    result = pipeline.ingest_market_data(
        object(), [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10), only_tickers={"NOPE"}
    )

    assert result["status"] == "partial"
    assert result["tickers_unmatched"] == 1


def test_a_partly_matching_ticker_request_still_prices_what_did_match(monkeypatch):
    save_calls = []
    _patch_market_data_source(monkeypatch, FakeMarketDataSource(), save_calls)
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    result = pipeline.ingest_market_data(
        object(),
        [],
        tracked,
        {},
        date(2026, 1, 1),
        date(2026, 1, 10),
        only_tickers={"AAPL", "NOPE"},
    )

    assert save_calls == [("yfinance", "equity", ["AAPL"])]  # the real one still ran
    assert result["rows_saved"] == 1
    assert result["tickers_unmatched"] == 1
    assert result["status"] == "partial"  # but the run is not clean


def test_an_empty_universe_with_no_ticker_filter_is_still_nothing_to_do():
    result = pipeline.ingest_market_data(object(), [], [], {}, date(2026, 1, 1), date(2026, 1, 10))

    assert result["status"] == "nothing_to_do"
    assert result["tickers_unmatched"] == 0


def _ok_ingest(monkeypatch, tracked=None, **kwargs):
    _patch_market_data_source(monkeypatch, FakeMarketDataSource(), [])
    tracked = tracked or [make_tracked_ticker(ticker="AAPL", asset_type="equity")]
    return pipeline.ingest_market_data(
        object(), [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10), **kwargs
    )


def test_ingest_result_reports_counts_and_ok_status(monkeypatch):
    result = _ok_ingest(
        monkeypatch,
        tracked=[
            make_tracked_ticker(ticker="AAPL", asset_type="equity"),
            make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
        ],
    )

    assert result["status"] == "ok"
    assert result["tickers_resolved"] == 2
    assert result["groups"] == 2
    assert result["groups_failed"] == 0
    assert result["rows_saved"] == 2


def test_ingest_result_distinguishes_nothing_to_do_from_a_failed_run(monkeypatch):
    nothing = pipeline.ingest_market_data(object(), [], [], {}, date(2026, 1, 1), date(2026, 1, 10))

    assert nothing["status"] == "nothing_to_do"
    assert nothing["tickers_resolved"] == 0
    assert nothing["rows_saved"] == 0

    fake_source = FlakyMarketDataSource(fail_asset_types={"etf"})
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda conn, source, at, df: len(df))
    partial = pipeline.ingest_market_data(
        object(),
        [],
        [
            make_tracked_ticker(ticker="FAIL.AS", asset_type="etf"),
            make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
        ],
        {},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    # Both saved-nothing-of-note runs, but only one of them went wrong.
    assert partial["status"] == "partial"
    assert partial["groups_failed"] == 1
    assert partial["rows_saved"] == 1


# --- backfill_market_data ----------------------------------------------------------------------


class FakeSingleMarketDataSource:
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


def test_backfill_silences_yfinances_own_noisy_no_data_logging(monkeypatch):
    # yfinance logs an ERROR-level "Failed download"/"Data doesn't exist" message via its own
    # 'yfinance' logger every time a fetch returns empty -- which, during a backfill, is the
    # expected outcome for every ticker once it's requested back past that ticker's actual
    # listing date. That noise drowns out our own clean per-ticker progress lines, so backfill
    # raises the yfinance logger's level instead of leaving it at the default.
    yf_logger = logging.getLogger("yfinance")
    original_level = yf_logger.level
    yf_logger.setLevel(logging.NOTSET)

    conn = object()
    # Observed from inside the fetch, not after the run: the silencing is scoped to the
    # backfill, so "still silenced afterwards" would be the leak, not the feature.
    levels_during_fetch = []

    class LevelObservingSource(FakeSingleMarketDataSource):
        def fetch(self, tickers, start_date, end_date):
            levels_during_fetch.append(logging.getLogger("yfinance").level)
            return super().fetch(tickers, start_date, end_date)

    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", LevelObservingSource)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    try:
        pipeline.backfill_market_data(
            conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
        )

        assert levels_during_fetch == [logging.CRITICAL]
    finally:
        yf_logger.setLevel(original_level)


def test_backfill_restores_the_yfinance_logger_level_afterwards(monkeypatch):
    yf_logger = logging.getLogger("yfinance")
    original_level = yf_logger.level
    yf_logger.setLevel(logging.INFO)

    monkeypatch.setitem(
        pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: FakeSingleMarketDataSource()
    )
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    try:
        pipeline.backfill_market_data(
            object(),
            [],
            [make_tracked_ticker(ticker="AAPL", asset_type="equity")],
            {},
            None,
            date(2026, 1, 1),
            delay_seconds=0,
        )

        assert yf_logger.level == logging.INFO
    finally:
        yf_logger.setLevel(original_level)


def test_backfill_restores_the_yfinance_logger_level_even_when_the_run_raises(monkeypatch):
    yf_logger = logging.getLogger("yfinance")
    original_level = yf_logger.level
    yf_logger.setLevel(logging.INFO)

    def exploding_resolver(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "_resolve_priceable_tickers", exploding_resolver)

    try:
        with pytest.raises(RuntimeError):
            pipeline.backfill_market_data(
                object(), [], [], {}, None, date(2026, 1, 1), delay_seconds=0
            )

        assert yf_logger.level == logging.INFO
    finally:
        yf_logger.setLevel(original_level)


def test_backfill_fetches_full_range_when_no_existing_data(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("AAPL", date(1900, 1, 1), date(2026, 1, 1))]


def test_backfill_fetches_only_gap_before_existing_floor(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dates",
        lambda conn, source, tickers: {"AAPL": date(2010, 1, 1)},
    )
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    # fetch()'s end is exclusive, so passing the floor itself already stops the day before
    # it -- no -1 day adjustment (that would silently drop the trading day right before it).
    assert fake_source.fetch_calls == [("AAPL", date(1900, 1, 1), date(2010, 1, 1))]


def test_backfill_skips_ticker_already_covered_to_earliest_date(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dates",
        lambda conn, source, tickers: {"AAPL": date(1900, 1, 1)},
    )
    save_calls = []
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda *a: save_calls.append(a))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    result = pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == []
    assert save_calls == []
    assert result["rows_saved"] == 0


def test_backfill_sleeps_after_fetched_and_failed_tickers_not_after_skips(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource(fail_tickers={"BAD"})
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="BAD", asset_type="equity"),
        make_tracked_ticker(ticker="COVERED", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dates",
        lambda conn, source, tickers: {"COVERED": date(1900, 1, 1)},
    )
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    sleep_calls = []
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: sleep_calls.append(s))

    pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=5
    )

    # AAPL (fetched successfully) and BAD (failed) both sleep; COVERED (skipped) does not.
    assert sleep_calls == [5, 5]


def test_backfill_continues_after_one_ticker_fails(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource(fail_tickers={"BAD"})
    tracked = [
        make_tracked_ticker(ticker="BAD", asset_type="equity"),
        make_tracked_ticker(ticker="GOOD", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    save_calls = []
    monkeypatch.setattr(
        pipeline,
        "save_raw_market_data",
        lambda conn, source, asset_type, df: save_calls.append(df) or len(df),
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    result = pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert result["rows_saved"] == 1  # only GOOD saved
    assert len(save_calls) == 1


def test_backfill_continues_after_one_ticker_fails_to_save(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [
        make_tracked_ticker(ticker="BAD", asset_type="equity"),
        make_tracked_ticker(ticker="GOOD", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})

    def fake_save(conn, source, asset_type, df):
        if df["ticker"].iloc[0] == "BAD":
            raise RuntimeError("numeric field overflow")
        return len(df)

    monkeypatch.setattr(pipeline, "save_raw_market_data", fake_save)
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    result = pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert result["rows_saved"] == 1  # only GOOD saved
    assert result["tickers_failed"] == 1
    record = next(r for r in caplog.records if "BAD" in r.message and "failed" in r.message)
    assert record.exc_info is not None, "traceback was dropped"


def test_a_failed_backfill_ticker_logs_its_traceback(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setitem(
        pipeline.MARKET_DATA_SOURCES,
        "yfinance",
        lambda: FakeSingleMarketDataSource(fail_tickers={"BAD"}),
    )
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        object(),
        [],
        [make_tracked_ticker(ticker="BAD", asset_type="equity")],
        {},
        None,
        date(2026, 1, 1),
        delay_seconds=0,
    )

    record = next(r for r in caplog.records if "failed" in r.message)
    assert record.exc_info is not None, "traceback was dropped"


def test_backfill_groups_by_source_and_type_and_queries_floors_per_group(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
        make_tracked_ticker(ticker="BTC-USD", asset_type="crypto"),
    ]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    floor_calls = []

    def fake_floors(conn, source, tickers):
        floor_calls.append((source, sorted(tickers)))
        return {}

    monkeypatch.setattr(pipeline, "get_earliest_dates", fake_floors)
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert sorted(floor_calls) == sorted([("yfinance", ["BTC-USD"]), ("yfinance", ["IWDA.AS"])])


def test_backfill_reads_floors_under_the_same_source_identifier_it_writes(monkeypatch):
    # The registry key and the source class's .name are two different strings that happen to
    # agree today. Reading floors under one and writing rows under the other means a source
    # registered under a differing key never sees its own stored data -- so it re-fetches its
    # full history on every run, forever.
    class AltNamedSource(FakeSingleMarketDataSource):
        name = "alt-source-name"

    floor_queries = []
    save_sources = []

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "alt-registry-key", AltNamedSource)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dates",
        lambda conn, source, tickers: floor_queries.append(source) or {},
    )
    monkeypatch.setattr(
        pipeline,
        "save_raw_market_data",
        lambda conn, source, asset_type, df: save_sources.append(source) or len(df),
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        object(),
        [],
        [make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="alt-registry-key")],
        {},
        None,
        date(2026, 1, 1),
        delay_seconds=0,
    )

    assert floor_queries == save_sources


def test_backfill_raises_on_unknown_market_data_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.backfill_market_data(
            object(), [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
        )


def test_backfill_returns_zero_when_no_tickers():
    result = pipeline.backfill_market_data(
        object(), [], [], {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert result["rows_saved"] == 0


def test_backfill_says_so_when_nothing_resolves(caplog):
    caplog.set_level(logging.INFO)
    result = pipeline.backfill_market_data(
        object(), [], [], {}, None, date(2026, 1, 1), delay_seconds=0
    )

    assert result["rows_saved"] == 0
    assert "nothing to do" in caplog.text.lower()


def test_backfill_filters_to_only_tickers_when_given(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="MSFT", asset_type="equity"),
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
    ]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn,
        [],
        tracked,
        {},
        date(1900, 1, 1),
        date(2026, 1, 1),
        delay_seconds=0,
        only_tickers={"MSFT"},
    )

    assert [call[0] for call in fake_source.fetch_calls] == ["MSFT"]


def test_backfill_only_tickers_can_target_holdings_derived_suffixed_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeSingleMarketDataSource()

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [
            ("0700", "Hong Kong Exchanges And Clearing Ltd"),
            ("AAPL", "NASDAQ"),
        ],
    )
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn,
        [fund],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK", "NASDAQ": None},
        date(1900, 1, 1),
        date(2026, 1, 1),
        delay_seconds=0,
        only_tickers={"0700.HK"},
    )

    assert [call[0] for call in fake_source.fetch_calls] == ["0700.HK"]


def test_backfill_applies_ticker_overrides_for_holdings_derived_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeSingleMarketDataSource()

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("BRKB", "New York Stock Exchange Inc.")],
    )
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn,
        [fund],
        [],
        {"New York Stock Exchange Inc.": None},
        date(1900, 1, 1),
        date(2026, 1, 1),
        delay_seconds=0,
        ticker_overrides={"BRKB": "BRK-B"},
    )

    assert [call[0] for call in fake_source.fetch_calls] == ["BRK-B"]


def test_backfill_warns_when_a_requested_ticker_matches_nothing(monkeypatch, caplog):
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)
    monkeypatch.setitem(
        pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: FakeSingleMarketDataSource()
    )

    with caplog.at_level(logging.WARNING):
        pipeline.backfill_market_data(
            object(),
            [],
            [make_tracked_ticker(ticker="AAPL", asset_type="equity")],
            {},
            None,
            date(2026, 1, 1),
            delay_seconds=0,
            only_tickers={"NOPE"},
        )

    assert "NOPE" in caplog.text


def test_backfill_reports_unmatched_ticker_requests_the_same_way(monkeypatch):
    monkeypatch.setitem(
        pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: FakeSingleMarketDataSource()
    )
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    result = pipeline.backfill_market_data(
        object(),
        [],
        [make_tracked_ticker(ticker="AAPL", asset_type="equity")],
        {},
        None,
        date(2026, 1, 1),
        delay_seconds=0,
        only_tickers={"NOPE"},
    )

    assert result["status"] == "partial"
    assert result["tickers_unmatched"] == 1


# --- backfill resume semantics (earliest_date omitted) ---------------------------------------
#
# yfinance returns a ticker's whole requested range in one call, so a ticker either got its full
# history or none of it -- resume granularity is already per-ticker. That makes "has any stored
# rows" a sound "already done" signal, and it's what makes a rerun cheap. Passing an explicit
# earliest_date opts back into gap-filling, for deliberately reaching further back than a
# previous run did.


def test_backfill_without_earliest_date_skips_tickers_that_already_have_rows(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dates",
        # AAPL's real listing date -- still far above any sane explicit earliest_date, which is
        # exactly why the old `fetch_end <= earliest_date` skip never fired on a rerun.
        lambda conn, source, tickers: {"AAPL": date(1980, 12, 12)},
    )
    save_calls = []
    monkeypatch.setattr(pipeline, "save_raw_market_data", lambda *a: save_calls.append(a))
    sleeps = []
    monkeypatch.setattr("data_store.pipeline.time.sleep", sleeps.append)

    pipeline.backfill_market_data(conn, [], tracked, {}, None, date(2026, 1, 1), delay_seconds=5)

    assert fake_source.fetch_calls == []
    assert save_calls == []
    assert sleeps == []


def test_backfill_without_earliest_date_fetches_tickers_with_no_rows(monkeypatch):
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(conn, [], tracked, {}, None, date(2026, 1, 1), delay_seconds=0)

    assert fake_source.fetch_calls == [("AAPL", pipeline.UNBOUNDED_EARLIEST_DATE, date(2026, 1, 1))]


def test_backfill_with_explicit_earliest_date_still_fills_the_gap(monkeypatch):
    # The escape hatch: an explicit date means "reach further back than last time", so a ticker
    # with rows is not skipped -- it gets the stretch below its floor.
    conn = object()
    fake_source = FakeSingleMarketDataSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dates",
        lambda conn, source, tickers: {"AAPL": date(2010, 1, 1)},
    )
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn, [], tracked, {}, date(1970, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("AAPL", date(1970, 1, 1), date(2010, 1, 1))]


def test_backfill_result_separates_skipped_fetched_and_failed(monkeypatch):
    fake_source = FakeSingleMarketDataSource(fail_tickers={"BAD"})
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dates",
        # DONE already has history, so it is skipped rather than fetched.
        lambda conn, source, tickers: {"DONE": date(2020, 1, 1)},
    )
    monkeypatch.setattr(
        pipeline, "save_raw_market_data", lambda conn, source, asset_type, df: len(df)
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    result = pipeline.backfill_market_data(
        object(),
        [],
        [
            make_tracked_ticker(ticker="NEW", asset_type="equity"),
            make_tracked_ticker(ticker="DONE", asset_type="equity"),
            make_tracked_ticker(ticker="BAD", asset_type="equity"),
        ],
        {},
        None,
        date(2026, 1, 1),
        delay_seconds=0,
    )

    assert result["status"] == "partial"  # BAD failed
    assert result["tickers_resolved"] == 3
    assert result["tickers_fetched"] == 1
    assert result["tickers_skipped"] == 1
    assert result["tickers_failed"] == 1
    assert result["rows_saved"] == 1


def test_backfill_result_reports_nothing_to_do(monkeypatch):
    result = pipeline.backfill_market_data(
        object(), [], [], {}, None, date(2026, 1, 1), delay_seconds=0
    )

    assert result["status"] == "nothing_to_do"
    assert result["tickers_resolved"] == 0
    assert result["rows_saved"] == 0


@pytest.mark.parametrize("flow", ["ingest_market_data", "backfill_market_data"])
def test_every_flow_result_survives_a_process_boundary(monkeypatch, flow):
    # Stage 4 hands this back from a separate interpreter as an Airflow XCom, pickled there and
    # unpickled in Airflow's own environment where `data_store` is not importable. Anything
    # richer than plain builtins -- a dataclass, an enum, a date, a numpy int -- would fail to
    # unpickle there with ModuleNotFoundError. Keep the contract boring on purpose.
    monkeypatch.setattr(pipeline, "get_earliest_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]
    args = (object(), [], tracked, {})
    if flow == "ingest_market_data":
        _patch_market_data_source(monkeypatch, FakeMarketDataSource(), [])
        result = pipeline.ingest_market_data(*args, date(2026, 1, 1), date(2026, 1, 10))
    else:
        monkeypatch.setitem(
            pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: FakeSingleMarketDataSource()
        )
        monkeypatch.setattr(pipeline, "save_raw_market_data", lambda conn, source, at, df: len(df))
        result = pipeline.backfill_market_data(*args, None, date(2026, 1, 1), delay_seconds=0)

    assert isinstance(result, dict)
    for key, value in result.items():
        assert type(key) is str, f"{key!r} is not a plain str"
        assert type(value) in (int, str), f"{key}={value!r} is {type(value).__name__}"
    assert pickle.loads(pickle.dumps(result)) == result
    # The keys every flow guarantees, so a caller can check any of them uniformly.
    assert {"status", "tickers_resolved", "rows_saved"} <= result.keys()


# --- ticker resolution (unchanged by the market-data merge) ------------------------------------


def test_resolve_yfinance_ticker_zero_pads_hong_kong_tickers():
    result = pipeline._resolve_yfinance_ticker(
        "1", "Hong Kong Exchanges And Clearing Ltd", {"Hong Kong Exchanges And Clearing Ltd": ".HK"}
    )

    assert result == "0001.HK"


def test_resolve_yfinance_ticker_leaves_four_digit_hong_kong_tickers_unpadded():
    result = pipeline._resolve_yfinance_ticker(
        "0700",
        "Hong Kong Exchanges And Clearing Ltd",
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
    )

    assert result == "0700.HK"


def test_resolve_yfinance_ticker_dedupes_trailing_dot_for_london():
    result = pipeline._resolve_yfinance_ticker(
        "BP.", "London Stock Exchange", {"London Stock Exchange": ".L"}
    )

    assert result == "BP.L"


def test_resolve_yfinance_ticker_leaves_non_dotted_london_tickers_alone():
    result = pipeline._resolve_yfinance_ticker(
        "HSBA", "London Stock Exchange", {"London Stock Exchange": ".L"}
    )

    assert result == "HSBA.L"


def test_resolve_yfinance_ticker_hyphenates_mid_string_dot_for_london():
    # "BT.A" (BT Group's A shares) uses "." as a class-share separator, not a trailing
    # full-stop -- verified against real yfinance that this needs "-", not ".", same as the
    # Nordic/Toronto class-share cases.
    result = pipeline._resolve_yfinance_ticker(
        "BT.A", "London Stock Exchange", {"London Stock Exchange": ".L"}
    )

    assert result == "BT-A.L"


@pytest.mark.parametrize(
    "exchange,suffix",
    [
        ("Nasdaq Omx Nordic", ".ST"),
        ("Nasdaq Omx Helsinki Ltd.", ".HE"),
        ("Omx Nordic Exchange Copenhagen A/S", ".CO"),
        ("Oslo Bors Asa", ".OL"),
    ],
)
def test_resolve_yfinance_ticker_hyphenates_space_for_nordic_exchanges(exchange, suffix):
    result = pipeline._resolve_yfinance_ticker("VOLV B", exchange, {exchange: suffix})

    assert result == f"VOLV-B{suffix}"


def test_resolve_yfinance_ticker_hyphenates_dot_for_toronto():
    result = pipeline._resolve_yfinance_ticker(
        "BBD.B", "Toronto Stock Exchange", {"Toronto Stock Exchange": ".TO"}
    )

    assert result == "BBD-B.TO"


def test_resolve_yfinance_ticker_leaves_other_exchanges_unnormalized():
    result = pipeline._resolve_yfinance_ticker("AAPL", "NASDAQ", {"NASDAQ": None})

    assert result == "AAPL"


def test_resolve_yfinance_ticker_override_bypasses_exchange_resolution_entirely():
    # HEIA's real problem is a mistagged source exchange -- the override must win even when
    # the (wrong) exchange isn't in exchange_suffixes at all, which would otherwise warn+skip.
    result = pipeline._resolve_yfinance_ticker(
        "HEIA", "NASDAQ", {}, ticker_overrides={"HEIA": "HEIA.AS"}
    )

    assert result == "HEIA.AS"


def test_resolve_yfinance_ticker_override_wins_even_when_exchange_resolves_fine():
    # BRKB's exchange (NYSE, suffix null) resolves fine on its own -- the override still wins,
    # since the raw ticker syntax itself ("BRKB") is what's wrong, not the exchange mapping.
    result = pipeline._resolve_yfinance_ticker(
        "BRKB",
        "New York Stock Exchange Inc.",
        {"New York Stock Exchange Inc.": None},
        ticker_overrides={"BRKB": "BRK-B"},
    )

    assert result == "BRK-B"


def test_resolve_yfinance_ticker_no_override_falls_through_to_normal_resolution():
    result = pipeline._resolve_yfinance_ticker(
        "AAPL", "NASDAQ", {"NASDAQ": None}, ticker_overrides={"BRKB": "BRK-B"}
    )

    assert result == "AAPL"


def test_resolve_skips_placeholder_dash_tickers(monkeypatch):
    # iShares uses "-" for lines with no real symbol. It survives the emptiness guard, and if
    # its exchange happens to be mapped it becomes a bogus request like "-.L".
    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda conn, fund_key: [("-", "London Stock Exchange"), ("HSBA", "London Stock Exchange")],
    )

    resolved = pipeline._resolve_priceable_tickers(
        object(), [make_fund()], [], {"London Stock Exchange": ".L"}
    )

    assert set(resolved) == {"HSBA.L"}
