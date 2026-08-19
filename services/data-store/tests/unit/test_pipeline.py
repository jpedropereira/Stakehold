import logging
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
        return [("AAPL", "NASDAQ"), ("MSFT", "NASDAQ")]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(
        conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 2
    assert get_tickers_calls == [(conn, fund.key)]
    assert save_calls == [("yfinance", "equity", ["AAPL", "MSFT"])]


def test_ingest_daily_ohlcv_suffixes_holdings_derived_ticker_by_exchange(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("0700", "Hong Kong Exchanges And Clearing Ltd")],
    )
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(
        conn,
        [fund],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    assert save_calls == [("yfinance", "equity", ["0700.HK"])]


def test_ingest_daily_ohlcv_skips_and_warns_on_unmapped_exchange(monkeypatch, caplog):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("AAPL", "NASDAQ"), ("0005", "NO MARKET (E.G. UNLISTED)")],
    )
    _patch_price_source(monkeypatch, fake_source, save_calls)

    with caplog.at_level("WARNING"):
        count = pipeline.ingest_daily_ohlcv(
            conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
        )

    assert count == 1
    assert save_calls == [("yfinance", "equity", ["AAPL"])]
    assert "0005" in caplog.text
    assert "NO MARKET (E.G. UNLISTED)" in caplog.text


def test_ingest_daily_ohlcv_prices_tracked_tickers_with_their_own_asset_type(monkeypatch):
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]

    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 1
    assert save_calls == [("yfinance", "etf", ["IWDA.AS"])]


def test_ingest_daily_ohlcv_tracked_ticker_never_suffix_resolved(monkeypatch):
    # tracked_tickers entries are already fully yfinance-ready -- exchange_suffixes having no
    # (or any) entry for them must never matter, since they never go through resolution at all.
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]

    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(
        conn, [], tracked, {"Euronext Amsterdam": ".AS"}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 1
    assert save_calls == [("yfinance", "etf", ["IWDA.AS"])]


def test_ingest_daily_ohlcv_dedupes_ticker_in_both_holdings_and_tracked(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    # BABA is both a fund holding and an explicitly tracked ticker (both asset_type=equity
    # here, so they land in the same group) -- must be fetched once, not twice.
    monkeypatch.setattr(
        pipeline, "get_latest_tickers", lambda c, k: [("BABA", "NASDAQ"), ("AAPL", "NASDAQ")]
    )
    tracked = [make_tracked_ticker(ticker="BABA", asset_type="equity")]
    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(
        conn, [fund], tracked, {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

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
    monkeypatch.setattr(pipeline, "get_latest_tickers", lambda c, k: [("WEIRD", "NASDAQ")])
    tracked = [make_tracked_ticker(ticker="WEIRD", asset_type="etf")]
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(
        conn, [fund], tracked, {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

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

    count = pipeline.ingest_daily_ohlcv(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

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
        return {
            "fund_a": [("AAPL", "NASDAQ"), ("MSFT", "NASDAQ")],
            "fund_b": [("AAPL", "NASDAQ"), ("NVDA", "NASDAQ")],
        }[fund_key]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(
        conn, [fund_a, fund_b], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["AAPL", "MSFT", "NVDA"]


def test_ingest_daily_ohlcv_dedupes_on_resolved_suffixed_ticker(monkeypatch):
    fund_a = make_fund(key="fund_a")
    fund_b = make_fund(key="fund_b")
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    # Same raw ticker + exchange combo held by two funds -- must resolve to the same suffixed
    # ticker and be fetched once, proving dedup happens post-suffix, not pre-suffix.
    def fake_get_latest_tickers(conn_arg, fund_key):
        return {
            "fund_a": [("0700", "Hong Kong Exchanges And Clearing Ltd")],
            "fund_b": [("0700", "Hong Kong Exchanges And Clearing Ltd")],
        }[fund_key]

    monkeypatch.setattr(pipeline, "get_latest_tickers", fake_get_latest_tickers)
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(
        conn,
        [fund_a, fund_b],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["0700.HK"]


def test_ingest_daily_ohlcv_strips_and_dedupes_raw_holdings_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
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
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(
        conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert fake_source.fetch_calls[0][0] == ["AAPL", "MSFT"]


def test_ingest_daily_ohlcv_returns_zero_when_no_tickers():
    count = pipeline.ingest_daily_ohlcv(object(), [], [], {}, date(2026, 1, 1), date(2026, 1, 10))

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

    count = pipeline.ingest_daily_ohlcv(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert count == 0
    assert save_calls == []


def test_ingest_daily_ohlcv_raises_on_unknown_price_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.ingest_daily_ohlcv(object(), [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))


def test_ingest_daily_ohlcv_filters_to_only_tickers_when_given(monkeypatch):
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="MSFT", asset_type="equity"),
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
    ]

    _patch_price_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_daily_ohlcv(
        conn,
        [],
        tracked,
        {},
        date(2026, 1, 1),
        date(2026, 1, 10),
        only_tickers={"MSFT"},
    )

    assert count == 1
    assert save_calls == [("yfinance", "equity", ["MSFT"])]


def test_ingest_daily_ohlcv_only_tickers_can_target_holdings_derived_suffixed_tickers(
    monkeypatch,
):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [
            ("0700", "Hong Kong Exchanges And Clearing Ltd"),
            ("AAPL", "NASDAQ"),
        ],
    )
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(
        conn,
        [fund],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK", "NASDAQ": None},
        date(2026, 1, 1),
        date(2026, 1, 10),
        only_tickers={"0700.HK"},
    )

    assert save_calls == [("yfinance", "equity", ["0700.HK"])]


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


def test_ingest_daily_ohlcv_applies_ticker_overrides_for_holdings_derived_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("BRKB", "New York Stock Exchange Inc.")],
    )
    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(
        conn,
        [fund],
        [],
        {"New York Stock Exchange Inc.": None},
        date(2026, 1, 1),
        date(2026, 1, 10),
        ticker_overrides={"BRKB": "BRK-B"},
    )

    assert save_calls == [("yfinance", "equity", ["BRK-B"])]


def test_ingest_daily_ohlcv_logs_run_summary(monkeypatch, capsys):
    # Observability requirement: ingest_daily_ohlcv used to be near-silent (only the
    # unmapped-exchange warning) -- must print enough to understand what happened without
    # querying the DB, mirroring ingest_dividend_events's print-based progress.
    conn = object()
    fake_source = FakePriceSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    _patch_price_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_daily_ohlcv(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    out = capsys.readouterr().out
    assert "1" in out  # resolved ticker count appears somewhere in the summary
    assert "AAPL" not in out  # summary is count-based, not a per-ticker dump


def test_ingest_daily_ohlcv_logs_no_price_data_distinctly(monkeypatch, capsys):
    conn = object()

    class EmptyPriceSource:
        name = "empty_source"

        def fetch(self, tickers, start_date, end_date):
            return pd.DataFrame()

    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: EmptyPriceSource())
    monkeypatch.setattr(pipeline, "save_prices", lambda *a: len(a[-1]))

    pipeline.ingest_daily_ohlcv(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert "no price data" in capsys.readouterr().out.lower()


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
    fake_source = FakeSinglePriceSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    try:
        pipeline.backfill_historical_ohlcv(
            conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
        )

        assert yf_logger.level >= logging.CRITICAL
    finally:
        yf_logger.setLevel(original_level)


def test_backfill_fetches_full_range_when_no_existing_data(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
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
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
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
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
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
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=5
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
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
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
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert sorted(floor_calls) == sorted([("yfinance", ["BTC-USD"]), ("yfinance", ["IWDA.AS"])])


def test_backfill_raises_on_unknown_price_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.backfill_historical_ohlcv(
            object(), [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
        )


def test_backfill_returns_zero_when_no_tickers():
    count = pipeline.backfill_historical_ohlcv(
        object(), [], [], {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert count == 0


def test_backfill_filters_to_only_tickers_when_given(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="MSFT", asset_type="equity"),
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
    ]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
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
    fake_source = FakeSinglePriceSource()

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [
            ("0700", "Hong Kong Exchanges And Clearing Ltd"),
            ("AAPL", "NASDAQ"),
        ],
    )
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
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
    fake_source = FakeSinglePriceSource()

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("BRKB", "New York Stock Exchange Inc.")],
    )
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
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


class FakeDividendSource:
    name = "yfinance"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append((sorted(tickers), start_date, end_date))
        return pd.DataFrame({"ticker": tickers, "ex_date": [start_date] * len(tickers)})


def _patch_dividend_source(monkeypatch, fake_source, save_calls):
    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "save_dividends",
        lambda conn_arg, source, asset_type, df: save_calls.append(
            (source, asset_type, sorted(df["ticker"]))
        )
        or len(df),
    )


def test_ingest_dividend_events_prices_holdings_derived_tickers_as_equity(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline, "get_latest_tickers", lambda c, k: [("AAPL", "NASDAQ"), ("MSFT", "NASDAQ")]
    )
    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_dividend_events(
        conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 2
    assert save_calls == [("yfinance", "equity", ["AAPL", "MSFT"])]


def test_ingest_dividend_events_suffixes_holdings_derived_ticker_by_exchange(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("0700", "Hong Kong Exchanges And Clearing Ltd")],
    )
    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_dividend_events(
        conn,
        [fund],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    assert save_calls == [("yfinance", "equity", ["0700.HK"])]


def test_ingest_dividend_events_skips_and_warns_on_unmapped_exchange(monkeypatch, caplog):
    fund = make_fund()
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("AAPL", "NASDAQ"), ("0005", "NO MARKET (E.G. UNLISTED)")],
    )
    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    with caplog.at_level("WARNING"):
        count = pipeline.ingest_dividend_events(
            conn, [fund], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
        )

    assert count == 1
    assert save_calls == [("yfinance", "equity", ["AAPL"])]
    assert "0005" in caplog.text
    assert "NO MARKET (E.G. UNLISTED)" in caplog.text


def test_ingest_dividend_events_prices_tracked_tickers_with_their_own_asset_type(monkeypatch):
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]

    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_dividend_events(
        conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 1
    assert save_calls == [("yfinance", "etf", ["IWDA.AS"])]


def test_ingest_dividend_events_dedupes_ticker_in_both_holdings_and_tracked(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline, "get_latest_tickers", lambda c, k: [("BABA", "NASDAQ"), ("AAPL", "NASDAQ")]
    )
    tracked = [make_tracked_ticker(ticker="BABA", asset_type="equity")]
    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_dividend_events(
        conn, [fund], tracked, {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 2
    assert len(fake_source.fetch_calls) == 1
    assert fake_source.fetch_calls[0][0] == ["AAPL", "BABA"]


def test_ingest_dividend_events_groups_by_asset_type_for_separate_save_calls(monkeypatch):
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []
    tracked = [
        make_tracked_ticker(ticker="IWDA.AS", asset_type="etf"),
        make_tracked_ticker(ticker="CSU.TO", asset_type="equity"),
    ]

    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_dividend_events(
        conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 2
    assert sorted(save_calls) == sorted(
        [("yfinance", "etf", ["IWDA.AS"]), ("yfinance", "equity", ["CSU.TO"])]
    )
    assert len(fake_source.fetch_calls) == 2


def test_ingest_dividend_events_returns_zero_when_no_tickers():
    count = pipeline.ingest_dividend_events(
        object(), [], [], {}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 0


def test_ingest_dividend_events_returns_zero_when_source_has_no_data(monkeypatch):
    conn = object()

    class EmptyDividendSource:
        name = "empty_source"

        def fetch(self, tickers, start_date, end_date):
            return pd.DataFrame()

    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]
    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: EmptyDividendSource())
    save_calls = []
    monkeypatch.setattr(pipeline, "save_dividends", lambda *a: save_calls.append(a) or len(a[-1]))

    count = pipeline.ingest_dividend_events(
        conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10)
    )

    assert count == 0
    assert save_calls == []


def test_ingest_dividend_events_raises_on_unknown_dividend_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.ingest_dividend_events(
            object(), [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10)
        )


def test_ingest_dividend_events_filters_to_only_tickers_when_given(monkeypatch):
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="MSFT", asset_type="equity"),
    ]

    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    count = pipeline.ingest_dividend_events(
        conn,
        [],
        tracked,
        {},
        date(2026, 1, 1),
        date(2026, 1, 10),
        only_tickers={"MSFT"},
    )

    assert count == 1
    assert save_calls == [("yfinance", "equity", ["MSFT"])]


def test_ingest_dividend_events_only_tickers_can_target_holdings_derived_suffixed_tickers(
    monkeypatch,
):
    fund = make_fund()
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline,
        "get_latest_tickers",
        lambda c, k: [("0700", "Hong Kong Exchanges And Clearing Ltd"), ("AAPL", "NASDAQ")],
    )
    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_dividend_events(
        conn,
        [fund],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK", "NASDAQ": None},
        date(2026, 1, 1),
        date(2026, 1, 10),
        only_tickers={"0700.HK"},
    )

    assert save_calls == [("yfinance", "equity", ["0700.HK"])]


def test_ingest_dividend_events_applies_ticker_overrides_for_holdings_derived_tickers(
    monkeypatch,
):
    fund = make_fund()
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []

    monkeypatch.setattr(
        pipeline, "get_latest_tickers", lambda c, k: [("BRKB", "New York Stock Exchange Inc.")]
    )
    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_dividend_events(
        conn,
        [fund],
        [],
        {"New York Stock Exchange Inc.": None},
        date(2026, 1, 1),
        date(2026, 1, 10),
        ticker_overrides={"BRKB": "BRK-B"},
    )

    assert save_calls == [("yfinance", "equity", ["BRK-B"])]


def test_ingest_dividend_events_logs_run_summary(monkeypatch, capsys):
    # Observability requirement: unlike ingest_daily_ohlcv (near-silent today),
    # ingest_dividend_events must print enough to understand what happened without querying the
    # DB -- resolved ticker/group counts up front, and a per-group outcome (rows found, or an
    # explicit "no dividend data" distinguishing a real zero-dividend result, e.g. an
    # accumulating ETF, from a fetch that returned nothing). print(), not logger.info -- this
    # codebase has no logging.basicConfig, so logger.info is silently swallowed by default
    # (confirmed via a live CLI dry run); print(..., flush=True) is what actually surfaces, the
    # same style backfill_historical_ohlcv already uses.
    conn = object()
    fake_source = FakeDividendSource()
    save_calls = []
    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]

    _patch_dividend_source(monkeypatch, fake_source, save_calls)

    pipeline.ingest_dividend_events(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    out = capsys.readouterr().out
    assert "1" in out  # resolved ticker count appears somewhere in the summary
    assert "IWDA.AS" not in out  # summary is count-based, not a per-ticker dump


def test_ingest_dividend_events_logs_no_dividend_data_distinctly(monkeypatch, capsys):
    conn = object()

    class EmptyDividendSource:
        name = "empty_source"

        def fetch(self, tickers, start_date, end_date):
            return pd.DataFrame()

    tracked = [make_tracked_ticker(ticker="IWDA.AS", asset_type="etf")]
    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: EmptyDividendSource())
    monkeypatch.setattr(pipeline, "save_dividends", lambda *a: len(a[-1]))

    pipeline.ingest_dividend_events(conn, [], tracked, {}, date(2026, 1, 1), date(2026, 1, 10))

    assert "no dividend data" in capsys.readouterr().out.lower()


class FakeSingleDividendSource:
    name = "yfinance"

    def __init__(self, *, fail_tickers=None):
        self.fetch_calls = []
        self.fail_tickers = fail_tickers or set()

    def fetch(self, tickers, start_date, end_date):
        ticker = tickers[0]
        self.fetch_calls.append((ticker, start_date, end_date))
        if ticker in self.fail_tickers:
            raise RuntimeError(f"{ticker} failed")
        return pd.DataFrame({"ticker": [ticker], "ex_date": [start_date]})


def test_backfill_dividends_fetches_full_range_when_no_existing_data(monkeypatch):
    conn = object()
    fake_source = FakeSingleDividendSource()
    tracked = [make_tracked_ticker(ticker="CSU.TO", asset_type="equity")]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dividend_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_dividends", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("CSU.TO", date(1900, 1, 1), date(2026, 1, 1))]


def test_backfill_dividends_fetches_only_gap_before_existing_floor(monkeypatch):
    conn = object()
    fake_source = FakeSingleDividendSource()
    tracked = [make_tracked_ticker(ticker="CSU.TO", asset_type="equity")]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dividend_dates",
        lambda conn, source, tickers: {"CSU.TO": date(2008, 3, 17)},
    )
    monkeypatch.setattr(pipeline, "save_dividends", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("CSU.TO", date(1900, 1, 1), date(2008, 3, 17))]


def test_backfill_dividends_skips_ticker_already_covered_to_earliest_date(monkeypatch):
    conn = object()
    fake_source = FakeSingleDividendSource()
    tracked = [make_tracked_ticker(ticker="CSU.TO", asset_type="equity")]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dividend_dates",
        lambda conn, source, tickers: {"CSU.TO": date(1900, 1, 1)},
    )
    save_calls = []
    monkeypatch.setattr(pipeline, "save_dividends", lambda *a: save_calls.append(a))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    count = pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == []
    assert save_calls == []
    assert count == 0


def test_backfill_dividends_sleeps_after_fetched_and_failed_tickers_not_after_skips(monkeypatch):
    conn = object()
    fake_source = FakeSingleDividendSource(fail_tickers={"BAD"})
    tracked = [
        make_tracked_ticker(ticker="CSU.TO", asset_type="equity"),
        make_tracked_ticker(ticker="BAD", asset_type="equity"),
        make_tracked_ticker(ticker="COVERED", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dividend_dates",
        lambda conn, source, tickers: {"COVERED": date(1900, 1, 1)},
    )
    monkeypatch.setattr(pipeline, "save_dividends", lambda conn, source, asset_type, df: len(df))
    sleep_calls = []
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: sleep_calls.append(s))

    pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=5
    )

    assert sleep_calls == [5, 5]


def test_backfill_dividends_continues_after_one_ticker_fails(monkeypatch):
    conn = object()
    fake_source = FakeSingleDividendSource(fail_tickers={"BAD"})
    tracked = [
        make_tracked_ticker(ticker="BAD", asset_type="equity"),
        make_tracked_ticker(ticker="GOOD", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dividend_dates", lambda conn, source, tickers: {})
    save_calls = []
    monkeypatch.setattr(
        pipeline,
        "save_dividends",
        lambda conn, source, asset_type, df: save_calls.append(df) or len(df),
    )
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    count = pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert count == 1  # only GOOD saved
    assert len(save_calls) == 1


def test_backfill_dividends_raises_on_unknown_dividend_source():
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity", price_source="unknown_source")
    ]

    with pytest.raises(ValueError, match="unknown_source"):
        pipeline.backfill_dividend_events(
            object(), [], tracked, {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
        )


def test_backfill_dividends_returns_zero_when_no_tickers():
    count = pipeline.backfill_dividend_events(
        object(), [], [], {}, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert count == 0


def test_backfill_dividends_filters_to_only_tickers_when_given(monkeypatch):
    conn = object()
    fake_source = FakeSingleDividendSource()
    tracked = [
        make_tracked_ticker(ticker="AAPL", asset_type="equity"),
        make_tracked_ticker(ticker="MSFT", asset_type="equity"),
    ]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dividend_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_dividends", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_dividend_events(
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


def test_backfill_dividends_applies_ticker_overrides_for_holdings_derived_tickers(monkeypatch):
    fund = make_fund()
    conn = object()
    fake_source = FakeSingleDividendSource()

    monkeypatch.setattr(
        pipeline, "get_latest_tickers", lambda c, k: [("BRKB", "New York Stock Exchange Inc.")]
    )
    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dividend_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_dividends", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_dividend_events(
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


# --- backfill resume semantics (earliest_date omitted) ---------------------------------------
#
# yfinance returns a ticker's whole requested range in one call, so a ticker either got its full
# history or none of it -- resume granularity is already per-ticker. That makes "has any stored
# rows" a sound "already done" signal, and it's what makes a rerun cheap. Passing an explicit
# earliest_date opts back into gap-filling, for deliberately reaching further back than a
# previous run did.


def test_backfill_without_earliest_date_skips_tickers_that_already_have_rows(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_price_dates",
        # AAPL's real listing date -- still far above any sane explicit earliest_date, which is
        # exactly why the old `fetch_end <= earliest_date` skip never fired on a rerun.
        lambda conn, source, tickers: {"AAPL": date(1980, 12, 12)},
    )
    save_calls = []
    monkeypatch.setattr(pipeline, "save_prices", lambda *a: save_calls.append(a))
    sleeps = []
    monkeypatch.setattr("data_store.pipeline.time.sleep", sleeps.append)

    count = pipeline.backfill_historical_ohlcv(
        conn, [], tracked, {}, None, date(2026, 1, 1), delay_seconds=10
    )

    assert fake_source.fetch_calls == []
    assert save_calls == []
    assert sleeps == []  # a skip must never pay the rate-limit delay
    assert count == 0


def test_backfill_without_earliest_date_fetches_tickers_with_no_rows(monkeypatch):
    conn = object()
    fake_source = FakeSinglePriceSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_price_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_prices", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, {}, None, date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("AAPL", pipeline.UNBOUNDED_EARLIEST_DATE, date(2026, 1, 1))]


def test_backfill_with_explicit_earliest_date_still_fills_the_gap(monkeypatch):
    # The escape hatch: an explicit date means "reach further back than last time", so a ticker
    # with rows is not skipped -- it gets the stretch below its floor.
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
        conn, [], tracked, {}, date(1970, 1, 1), date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("AAPL", date(1970, 1, 1), date(2010, 1, 1))]


def test_backfill_dividends_without_earliest_date_skips_tickers_that_already_have_rows(
    monkeypatch,
):
    conn = object()
    fake_source = FakeSingleDividendSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_dividend_dates",
        lambda conn, source, tickers: {"AAPL": date(1987, 5, 11)},
    )
    save_calls = []
    monkeypatch.setattr(pipeline, "save_dividends", lambda *a: save_calls.append(a))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    count = pipeline.backfill_dividend_events(
        conn, [], tracked, {}, None, date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == []
    assert save_calls == []
    assert count == 0


def test_backfill_dividends_without_earliest_date_fetches_tickers_with_no_rows(monkeypatch):
    conn = object()
    fake_source = FakeSingleDividendSource()
    tracked = [make_tracked_ticker(ticker="AAPL", asset_type="equity")]

    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(pipeline, "get_earliest_dividend_dates", lambda conn, source, tickers: {})
    monkeypatch.setattr(pipeline, "save_dividends", lambda conn, source, asset_type, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_dividend_events(
        conn, [], tracked, {}, None, date(2026, 1, 1), delay_seconds=0
    )

    assert fake_source.fetch_calls == [("AAPL", pipeline.UNBOUNDED_EARLIEST_DATE, date(2026, 1, 1))]
