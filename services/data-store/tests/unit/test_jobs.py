"""Job-layer tests: that each run_* assembles its inputs and delegates correctly.

These were cli.py's tests before the job layer existed. They live here now because what they
actually exercise -- settings, fund selection, tracked tickers, ticker resolution, date windows,
the ticker filter, per-fund failure isolation -- is the job's behaviour, reachable without going
through argparse. test_cli.py keeps only what is genuinely about the command line.
"""

import logging
from datetime import date

import pytest
from data_store import jobs
from data_store.config_loader import (
    CurrenciesConfig,
    CurrencyConfig,
    FundConfig,
    TickerConfig,
    TickerResolution,
)
from data_store.settings import Settings


@pytest.fixture
def fake_settings(tmp_path):
    return Settings(
        database_url="postgresql://user:pass@localhost/db",
        config_path=tmp_path,
        # Deliberately a nonexistent path by default: tracked_tickers.yaml is optional, and
        # most jobs.py tests shouldn't need to care about it.
        tracked_tickers_path=tmp_path / "does-not-exist.yaml",
        exchange_suffixes_path=tmp_path / "yfinance_exchange_suffixes.yaml",
        regimes_csv_path=tmp_path / "regimes.csv",
        currencies_path=tmp_path / "currencies.yaml",
    )


def stub_resolution(monkeypatch, *, suffixes=None, overrides=None):
    """Point the job layer's ticker-resolution load at in-memory tables."""
    monkeypatch.setattr(
        jobs,
        "load_ticker_resolution",
        lambda path: TickerResolution(
            exchange_suffixes=suffixes or {}, ticker_overrides=overrides or {}
        ),
    )


@pytest.fixture(autouse=True)
def stub_load_ticker_resolution(monkeypatch):
    # yfinance_exchange_suffixes.yaml is load-bearing (unconditionally loaded, unlike
    # tracked_tickers.yaml), but most jobs.py tests don't exercise ticker resolution itself --
    # stub it to empty tables by default so those tests don't each need to care about it.
    stub_resolution(monkeypatch)


def flow_result(rows_saved=0, status="ok", **extra):
    """Minimal stand-in for a pipeline flow's return value, for stubs that only care that the
    CLI reads `rows_saved`/`status` off it rather than a bare int."""
    return {"status": status, "tickers_resolved": 1, "rows_saved": rows_saved, **extra}


def make_fund(key, enabled=True):
    return FundConfig(
        key=key,
        display_name=key,
        provider="ishares",
        url=f"https://example.com/{key}",
        isin=None,
        price_source="yfinance",
        enabled=enabled,
    )


def make_currencies(*, base="EUR", codes=("EUR", "USD", "GBP"), disabled=()):
    return CurrenciesConfig(
        base_currency=base,
        currencies=[CurrencyConfig(code=c, enabled=c not in disabled) for c in codes],
    )


def make_tracked_ticker(ticker, asset_type="etf", enabled=True):
    return TickerConfig(
        ticker=ticker,
        display_name=ticker,
        asset_type=asset_type,
        price_source="yfinance",
        enabled=enabled,
    )


def test_db_init_applies_schema(monkeypatch, fake_settings):
    calls = {}

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(
        jobs,
        "apply_schema",
        lambda conn, path: calls.setdefault("apply_schema", (conn, path)),
    )
    closed = []

    def fake_get_connection(url):
        calls["conn_url"] = url
        return FakeConn(closed)

    monkeypatch.setattr(jobs, "get_connection", fake_get_connection)

    result = jobs.run_db_init()

    assert result["status"] != "partial"
    assert calls["conn_url"] == "postgresql://user:pass@localhost/db"
    assert calls["apply_schema"][0].__class__.__name__ == "FakeConn"
    assert closed == [True]


class FakeConn:
    def __init__(self, closed):
        self._closed = closed

    def close(self):
        self._closed.append(True)


def test_ingest_holdings_runs_for_all_enabled_funds_by_default(monkeypatch, fake_settings):
    funds = [make_fund("a"), make_fund("b", enabled=False), make_fund("c")]
    closed = []
    ingested = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(
        jobs,
        "ingest_holdings",
        lambda conn, fund, today: ingested.append(fund.key) or 5,
    )

    result = jobs.run_ingest_holdings()

    assert result["status"] != "partial"
    assert ingested == ["a", "c"]
    assert closed == [True]


def test_ingest_holdings_runs_only_for_given_fund(monkeypatch, fake_settings):
    funds = [make_fund("a"), make_fund("b")]
    ingested = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_holdings",
        lambda conn, fund, today: ingested.append(fund.key) or 1,
    )

    jobs.run_ingest_holdings(fund="b")

    assert ingested == ["b"]


def test_ingest_holdings_raises_for_unknown_fund_key(monkeypatch, fake_settings):
    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [make_fund("a")])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    with pytest.raises(ValueError, match="unknown_key"):
        jobs.run_ingest_holdings(fund="unknown_key")


def test_ingest_holdings_prints_scrape_start_and_summary(monkeypatch, fake_settings, caplog):
    caplog.set_level(logging.INFO)
    funds = [make_fund("a"), make_fund("c")]

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(jobs, "ingest_holdings", lambda conn, fund, today: 5)

    jobs.run_ingest_holdings()

    out = caplog.text
    assert "scraping a" in out
    assert "scraping c" in out
    assert "complete: 10" in out  # 5 rows per fund, 2 funds
    assert "2 fund" in out


# --- run_ingest_market_data --------------------------------------------------------------------


def test_ingest_market_data_runs_for_all_enabled_funds_with_default_lookback(
    monkeypatch, fake_settings
):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    def fake_ingest(
        conn,
        passed_funds,
        tracked_tickers,
        exchange_suffixes,
        start_date,
        end_date,
        only_tickers=None,
        ticker_overrides=None,
    ):
        calls.append(
            (passed_funds, tracked_tickers, exchange_suffixes, start_date, end_date, only_tickers)
        )
        return flow_result(3)

    monkeypatch.setattr(jobs, "ingest_market_data", fake_ingest)

    result = jobs.run_ingest_market_data()

    assert result["status"] != "partial"
    assert len(calls) == 1
    passed_funds, tracked_tickers, exchange_suffixes, start_date, end_date, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert tracked_tickers == []  # tracked_tickers.yaml doesn't exist in fake_settings
    assert exchange_suffixes == {}
    assert only_tickers is None
    assert end_date == date.today()
    assert (end_date - start_date).days == 7


def test_ingest_market_data_respects_days_flag(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_market_data",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            (s, e)
        )
        or flow_result(1),
    )

    jobs.run_ingest_market_data(days=30)

    start_date, end_date = calls[0]
    assert (end_date - start_date).days == 30


def test_ingest_market_data_ticker_flag_passes_only_tickers_filter(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_market_data",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            (funds, only_tickers)
        )
        or flow_result(),
    )

    jobs.run_ingest_market_data(tickers=["BABA", "0700.HK"])

    passed_funds, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert only_tickers == {"BABA", "0700.HK"}


def test_ingest_market_data_without_ticker_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_market_data",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            only_tickers
        )
        or flow_result(),
    )

    jobs.run_ingest_market_data()

    assert calls[0] is None


def test_ingest_market_data_includes_enabled_tracked_tickers(monkeypatch, fake_settings, tmp_path):
    settings = Settings(
        database_url=fake_settings.database_url,
        config_path=fake_settings.config_path,
        tracked_tickers_path=tmp_path / "tracked_tickers.yaml",
        exchange_suffixes_path=fake_settings.exchange_suffixes_path,
        regimes_csv_path=fake_settings.regimes_csv_path,
        currencies_path=fake_settings.currencies_path,
    )
    (tmp_path / "tracked_tickers.yaml").write_text("tickers: []\n")  # just needs to exist
    tracked = [make_tracked_ticker("IWDA.AS"), make_tracked_ticker("BTC-USD", enabled=False)]
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "load_tracked_tickers", lambda path: tracked)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_market_data",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(tt)
        or flow_result(1),
    )

    jobs.run_ingest_market_data()

    assert [t.ticker for t in calls[0]] == ["IWDA.AS"]  # disabled BTC-USD excluded


def test_ingest_market_data_treats_missing_tracked_tickers_file_as_empty(
    monkeypatch, fake_settings
):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_market_data",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(tt)
        or flow_result(),
    )

    jobs.run_ingest_market_data()

    assert calls[0] == []


def test_ingest_market_data_loads_and_passes_exchange_suffixes(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    stub_resolution(monkeypatch, suffixes={"NASDAQ": None, "London Stock Exchange": ".L"})
    monkeypatch.setattr(
        jobs,
        "ingest_market_data",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(ex)
        or flow_result(),
    )

    jobs.run_ingest_market_data()

    assert calls[0] == {"NASDAQ": None, "London Stock Exchange": ".L"}


def test_ingest_market_data_loads_and_passes_ticker_overrides(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    stub_resolution(monkeypatch, overrides={"BRKB": "BRK-B"})
    monkeypatch.setattr(
        jobs,
        "ingest_market_data",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            ticker_overrides
        )
        or flow_result(),
    )

    jobs.run_ingest_market_data()

    assert calls[0] == {"BRKB": "BRK-B"}


def test_ingest_market_data_propagates_config_error_from_exchange_suffixes(
    monkeypatch, fake_settings
):
    from data_store.config_loader import ConfigError

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    def raise_config_error(path):
        raise ConfigError("bad exchange suffixes config")

    monkeypatch.setattr(jobs, "load_ticker_resolution", raise_config_error)

    with pytest.raises(ConfigError, match="bad exchange suffixes config"):
        jobs.run_ingest_market_data()


# --- run_backfill_market_data -------------------------------------------------------------------


def test_backfill_market_data_default_run(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    def fake_backfill(
        conn,
        passed_funds,
        tracked_tickers,
        exchange_suffixes,
        earliest_date,
        end_date,
        delay_seconds,
        only_tickers=None,
        ticker_overrides=None,
        force_refresh=False,
    ):
        calls.append(
            (
                passed_funds,
                tracked_tickers,
                exchange_suffixes,
                earliest_date,
                end_date,
                delay_seconds,
                only_tickers,
            )
        )
        return flow_result(3)

    monkeypatch.setattr(jobs, "backfill_market_data", fake_backfill)

    result = jobs.run_backfill_market_data()

    assert result["status"] != "partial"
    assert len(calls) == 1
    (
        passed_funds,
        tracked_tickers,
        exchange_suffixes,
        earliest_date,
        end_date,
        delay_seconds,
        only_tickers,
    ) = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert tracked_tickers == []
    assert exchange_suffixes == {}
    assert only_tickers is None
    # No --earliest-date means "resume": the pipeline skips tickers that already have
    # rows rather than re-requesting each one's full history on every rerun.
    assert earliest_date is None
    assert end_date == date.today()
    assert delay_seconds == 10.0


def test_backfill_market_data_respects_fund_flag(monkeypatch, fake_settings):
    funds = [make_fund("a"), make_fund("b")]
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(funds) or flow_result(),
    )

    jobs.run_backfill_market_data(fund="b")

    assert [f.key for f in calls[0]] == ["b"]


def test_backfill_market_data_respects_earliest_date_and_delay_flags(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append((ed, ds)) or flow_result(),
    )

    jobs.run_backfill_market_data(earliest_date="2000-06-15", delay_seconds=3.5)

    earliest_date, delay_seconds = calls[0]
    assert earliest_date == date(2000, 6, 15)
    assert delay_seconds == 3.5


def test_backfill_market_data_forwards_force_refresh(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(kw) or flow_result(),
    )

    jobs.run_backfill_market_data(force_refresh=True)
    jobs.run_backfill_market_data()

    assert calls[0]["force_refresh"] is True
    assert calls[1]["force_refresh"] is False


def test_backfill_market_data_ticker_flag_passes_only_tickers_filter(monkeypatch, fake_settings):
    # --ticker no longer pre-filters tracked_tickers.yaml and empties funds -- it passes an
    # only_tickers filter through to backfill_market_data, applied *after* suffix
    # resolution, so it can target holdings-derived tickers (e.g. "0700.HK") too, not just
    # tracked_tickers entries. funds/tracked_tickers loading stays untouched.
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, only_tickers=None, **kw: calls.append(
            (funds, only_tickers)
        )
        or flow_result(),
    )

    jobs.run_backfill_market_data(tickers=["BABA", "0700.HK"])

    passed_funds, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert only_tickers == {"BABA", "0700.HK"}


def test_backfill_market_data_without_ticker_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, only_tickers=None, **kw: calls.append(only_tickers)
        or flow_result(),
    )

    jobs.run_backfill_market_data()

    assert calls[0] is None


def test_backfill_market_data_treats_missing_tracked_tickers_file_as_empty(
    monkeypatch, fake_settings
):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(tt) or flow_result(),
    )

    jobs.run_backfill_market_data()

    assert calls[0] == []


def test_backfill_market_data_loads_and_passes_exchange_suffixes(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    stub_resolution(monkeypatch, suffixes={"NASDAQ": None})
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(ex) or flow_result(),
    )

    jobs.run_backfill_market_data()

    assert calls[0] == {"NASDAQ": None}


def test_backfill_market_data_loads_and_passes_ticker_overrides(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    stub_resolution(monkeypatch, overrides={"BRKB": "BRK-B"})
    monkeypatch.setattr(
        jobs,
        "backfill_market_data",
        lambda conn, funds, tt, ex, ed, end, ds, ticker_overrides=None, **kw: calls.append(
            ticker_overrides
        )
        or flow_result(),
    )

    jobs.run_backfill_market_data()

    assert calls[0] == {"BRKB": "BRK-B"}


def test_backfill_market_data_propagates_config_error_from_exchange_suffixes(
    monkeypatch, fake_settings
):
    from data_store.config_loader import ConfigError

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    def raise_config_error(path):
        raise ConfigError("bad exchange suffixes config")

    monkeypatch.setattr(jobs, "load_ticker_resolution", raise_config_error)

    with pytest.raises(ConfigError, match="bad exchange suffixes config"):
        jobs.run_backfill_market_data()


def test_ingest_holdings_continues_after_one_fund_fails(monkeypatch, fake_settings, caplog):
    caplog.set_level(logging.INFO)
    # Scraping is the flakiest step in the service (a real browser against a third-party site).
    # One fund's failure shouldn't cost every fund queued behind it, and the run must still
    # report what happened rather than dying before its summary.
    funds = [make_fund("a"), make_fund("b"), make_fund("c")]
    ingested = []

    def flaky_ingest(conn, fund, today):
        if fund.key == "b":
            raise RuntimeError("playwright timed out")
        ingested.append(fund.key)
        return 5

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: funds)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(jobs, "ingest_holdings", flaky_ingest)

    result = jobs.run_ingest_holdings()

    assert ingested == ["a", "c"]
    out = caplog.text
    assert "complete: 10" in out
    assert "1 fund(s) failed" in out
    # A run with a failure in it must not report success.
    assert result["status"] == "partial"


def test_tracked_tickers_file_present_but_empty_is_treated_as_none(tmp_path, monkeypatch):
    # The README documents this file as optional; a copied-but-not-yet-filled-in template is
    # the same situation as an absent one, not a hard error that breaks all four commands.
    path = tmp_path / "tracked_tickers.yaml"
    path.write_text("# everything still commented out\ntickers:\n")

    assert jobs._load_enabled_tracked_tickers(path) == []


# --- run_calculate_returns --------------------------------------------------------------------


def test_calculate_returns_opens_and_closes_a_connection(monkeypatch, fake_settings):
    closed = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(jobs, "calculate_returns", lambda conn, only_tickers=None: flow_result(5))

    result = jobs.run_calculate_returns()

    assert result["status"] != "partial"
    assert closed == [True]


def test_calculate_returns_passes_no_ticker_filter_by_default(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "calculate_returns",
        lambda conn, only_tickers=None: calls.append(only_tickers) or flow_result(),
    )

    jobs.run_calculate_returns()

    assert calls == [None]


def test_calculate_returns_ticker_flag_passes_only_tickers_filter(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "calculate_returns",
        lambda conn, only_tickers=None: calls.append(only_tickers) or flow_result(),
    )

    jobs.run_calculate_returns(tickers=["AAPL", "0700.HK"])

    assert calls == [{"AAPL", "0700.HK"}]


def test_calculate_returns_does_not_load_funds_or_tracked_tickers(monkeypatch, fake_settings):
    # Unlike the pricing flows, calculate-returns reads only what's already in
    # raw_market_data -- no fund/ticker-config coupling at all.
    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(jobs, "calculate_returns", lambda conn, only_tickers=None: flow_result())

    def fail(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr(jobs, "load_funds", fail)
    monkeypatch.setattr(jobs, "load_tracked_tickers", fail)
    monkeypatch.setattr(jobs, "load_ticker_resolution", fail)

    jobs.run_calculate_returns()


def test_a_failed_fund_scrape_logs_its_traceback(monkeypatch, fake_settings, caplog):
    # The exception message alone is often useless for the failure this actually catches --
    # "Timeout 60000ms exceeded" says nothing about which selector or step timed out. Airflow
    # task logs are the primary diagnostic artifact, so the stack trace has to reach them.
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_funds", lambda path: [make_fund("a")])
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    def exploding(conn, fund, today):
        raise RuntimeError("Timeout 60000ms exceeded")

    monkeypatch.setattr(jobs, "ingest_holdings", exploding)

    jobs.run_ingest_holdings()

    record = next(r for r in caplog.records if "failed" in r.message)
    assert record.exc_info is not None, "traceback was dropped"
    assert "Timeout 60000ms exceeded" in caplog.text
    assert "RuntimeError" in caplog.text  # from the formatted traceback


# --- run_ingest_fx_rates -------------------------------------------------------------------------


def test_ingest_fx_rates_loads_currencies_and_uses_default_seven_day_window(
    monkeypatch, fake_settings
):
    currencies = make_currencies()
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: currencies)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_fx_rates",
        lambda conn, c, s, e, only_currencies=None: calls.append((c, s, e, only_currencies))
        or flow_result(3),
    )

    result = jobs.run_ingest_fx_rates()

    assert result["status"] != "partial"
    assert len(calls) == 1
    passed_currencies, start_date, end_date, only_currencies = calls[0]
    assert passed_currencies is currencies
    assert only_currencies is None
    assert end_date == date.today()
    assert (end_date - start_date).days == 7


def test_ingest_fx_rates_respects_days_flag(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_fx_rates",
        lambda conn, c, s, e, only_currencies=None: calls.append((s, e)) or flow_result(1),
    )

    jobs.run_ingest_fx_rates(days=30)

    start_date, end_date = calls[0]
    assert (end_date - start_date).days == 30


def test_ingest_fx_rates_currency_flag_passes_only_currencies_filter(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_fx_rates",
        lambda conn, c, s, e, only_currencies=None: calls.append(only_currencies) or flow_result(),
    )

    jobs.run_ingest_fx_rates(currencies=["USD", "GBP"])

    assert calls[0] == {"USD", "GBP"}


def test_ingest_fx_rates_without_currency_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "ingest_fx_rates",
        lambda conn, c, s, e, only_currencies=None: calls.append(only_currencies) or flow_result(),
    )

    jobs.run_ingest_fx_rates()

    assert calls[0] is None


def test_ingest_fx_rates_closes_the_connection(monkeypatch, fake_settings):
    closed = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(
        jobs, "ingest_fx_rates", lambda conn, c, s, e, only_currencies=None: flow_result(1)
    )

    jobs.run_ingest_fx_rates()

    assert closed == [True]


# --- run_backfill_fx_rates -----------------------------------------------------------------------


def test_backfill_fx_rates_default_run(monkeypatch, fake_settings):
    currencies = make_currencies()
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: currencies)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    def fake_backfill(
        conn,
        passed_currencies,
        earliest_date,
        end_date,
        delay_seconds,
        only_currencies=None,
        force_refresh=False,
    ):
        calls.append((passed_currencies, earliest_date, end_date, delay_seconds, only_currencies))
        return flow_result(3)

    monkeypatch.setattr(jobs, "backfill_fx_rates", fake_backfill)

    result = jobs.run_backfill_fx_rates()

    assert result["status"] != "partial"
    assert len(calls) == 1
    passed_currencies, earliest_date, end_date, delay_seconds, only_currencies = calls[0]
    assert passed_currencies is currencies
    assert only_currencies is None
    # No --earliest-date means "resume" -- see pipeline._backfill_window.
    assert earliest_date is None
    assert end_date == date.today()
    assert delay_seconds == 10.0


def test_backfill_fx_rates_respects_earliest_date_and_delay_flags(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_fx_rates",
        lambda conn, c, ed, end, ds, **kw: calls.append((ed, ds)) or flow_result(),
    )

    jobs.run_backfill_fx_rates(earliest_date="2000-06-15", delay_seconds=3.5)

    earliest_date, delay_seconds = calls[0]
    assert earliest_date == date(2000, 6, 15)
    assert delay_seconds == 3.5


def test_backfill_fx_rates_forwards_force_refresh(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_fx_rates",
        lambda conn, c, ed, end, ds, **kw: calls.append(kw) or flow_result(),
    )

    jobs.run_backfill_fx_rates(force_refresh=True)
    jobs.run_backfill_fx_rates()

    assert calls[0]["force_refresh"] is True
    assert calls[1]["force_refresh"] is False


def test_backfill_fx_rates_currency_flag_passes_only_currencies_filter(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_fx_rates",
        lambda conn, c, ed, end, ds, only_currencies=None, **kw: calls.append(only_currencies)
        or flow_result(),
    )

    jobs.run_backfill_fx_rates(currencies=["USD"])

    assert calls[0] == {"USD"}


def test_backfill_fx_rates_without_currency_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        jobs,
        "backfill_fx_rates",
        lambda conn, c, ed, end, ds, only_currencies=None, **kw: calls.append(only_currencies)
        or flow_result(),
    )

    jobs.run_backfill_fx_rates()

    assert calls[0] is None


def test_backfill_fx_rates_closes_the_connection(monkeypatch, fake_settings):
    closed = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(
        jobs, "backfill_fx_rates", lambda conn, c, ed, end, ds, **kw: flow_result(1)
    )

    jobs.run_backfill_fx_rates()

    assert closed == [True]


# --- get_price_series / get_fx_series / get_ticker_info -- the read API's job layer ---------


def test_get_price_series_calls_price_series_with_a_connection_and_closes_it(
    monkeypatch, fake_settings
):
    closed = []
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(
        jobs,
        "price_series",
        lambda conn, ticker, source, start, end: calls.append((ticker, source, start, end))
        or {"ticker": ticker},
    )

    result = jobs.get_price_series(
        ticker="AAPL", source="yfinance", start=date(2024, 1, 1), end=date(2024, 1, 31)
    )

    assert result == {"ticker": "AAPL"}
    assert calls == [("AAPL", "yfinance", date(2024, 1, 1), date(2024, 1, 31))]
    assert closed == [True]


def test_get_price_series_propagates_ticker_not_found(monkeypatch, fake_settings):
    from data_store.read_flows import TickerNotFound

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn([]))

    def raise_not_found(conn, ticker, source, start, end):
        raise TickerNotFound(ticker)

    monkeypatch.setattr(jobs, "price_series", raise_not_found)

    with pytest.raises(TickerNotFound):
        jobs.get_price_series(
            ticker="NOPE", source="yfinance", start=date(2024, 1, 1), end=date(2024, 1, 31)
        )


def test_get_ticker_info_calls_ticker_info_and_closes_the_connection(monkeypatch, fake_settings):
    closed = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(
        jobs, "ticker_info", lambda conn, ticker, source: {"ticker": ticker, "exists": True}
    )

    result = jobs.get_ticker_info(ticker="AAPL", source="yfinance")

    assert result == {"ticker": "AAPL", "exists": True}
    assert closed == [True]


def test_get_fx_series_loads_currencies_config_and_closes_the_connection(
    monkeypatch, fake_settings
):
    closed = []
    calls = []

    monkeypatch.setattr(jobs, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(jobs, "load_currencies", lambda path: make_currencies())
    monkeypatch.setattr(jobs, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(
        jobs,
        "fx_rate_series",
        lambda conn, currencies_config, base, quote, source, start, end: calls.append(
            (currencies_config, base, quote, source, start, end)
        )
        or {"base_currency": base, "quote_currency": quote},
    )

    result = jobs.get_fx_series(
        base="EUR", quote="USD", source="yfinance", start=date(2024, 1, 1), end=date(2024, 1, 31)
    )

    assert result == {"base_currency": "EUR", "quote_currency": "USD"}
    assert calls[0][1:] == ("EUR", "USD", "yfinance", date(2024, 1, 1), date(2024, 1, 31))
    assert closed == [True]
