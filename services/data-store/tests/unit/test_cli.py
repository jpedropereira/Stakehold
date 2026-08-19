from datetime import date

import pytest
from data_store import cli
from data_store.config_loader import FundConfig, TickerConfig
from data_store.settings import Settings


@pytest.fixture
def fake_settings(tmp_path):
    return Settings(
        database_url="postgresql://user:pass@localhost/db",
        config_path=tmp_path,
        # Deliberately a nonexistent path by default: tracked_tickers.yaml is optional, and
        # most cli.py tests shouldn't need to care about it.
        tracked_tickers_path=tmp_path / "does-not-exist.yaml",
        exchange_suffixes_path=tmp_path / "yfinance_exchange_suffixes.yaml",
    )


@pytest.fixture(autouse=True)
def stub_load_exchange_suffixes(monkeypatch):
    # exchange_suffixes.yaml is load-bearing (unconditionally loaded, unlike
    # tracked_tickers.yaml), but most cli.py tests don't exercise suffix resolution itself --
    # stub it to an empty dict by default so those tests don't each need to care about it.
    monkeypatch.setattr(cli, "load_exchange_suffixes", lambda path: {})


@pytest.fixture(autouse=True)
def stub_load_ticker_overrides(monkeypatch):
    # ticker_overrides live in the same file/setting as exchange_suffixes and are loaded
    # unconditionally alongside it -- stub to empty by default for the same reason.
    monkeypatch.setattr(cli, "load_ticker_overrides", lambda path: {})


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

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(
        cli,
        "apply_schema",
        lambda conn, path: calls.setdefault("apply_schema", (conn, path)),
    )
    closed = []

    def fake_get_connection(url):
        calls["conn_url"] = url
        return FakeConn(closed)

    monkeypatch.setattr(cli, "get_connection", fake_get_connection)

    exit_code = cli.main(["db", "init"])

    assert exit_code == 0
    assert calls["conn_url"] == "postgresql://user:pass@localhost/db"
    assert calls["apply_schema"][0].__class__.__name__ == "FakeConn"
    assert closed == [True]


class FakeConn:
    def __init__(self, closed):
        self._closed = closed

    def close(self):
        self._closed.append(True)


def test_missing_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code != 0


def test_missing_db_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["db"])

    assert exc_info.value.code != 0


def test_ingest_holdings_runs_for_all_enabled_funds_by_default(monkeypatch, fake_settings):
    funds = [make_fund("a"), make_fund("b", enabled=False), make_fund("c")]
    closed = []
    ingested = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn(closed))
    monkeypatch.setattr(
        cli,
        "ingest_holdings",
        lambda conn, fund, today: ingested.append(fund.key) or 5,
    )

    exit_code = cli.main(["ingest-holdings"])

    assert exit_code == 0
    assert ingested == ["a", "c"]
    assert closed == [True]


def test_ingest_holdings_runs_only_for_given_fund(monkeypatch, fake_settings):
    funds = [make_fund("a"), make_fund("b")]
    ingested = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_holdings",
        lambda conn, fund, today: ingested.append(fund.key) or 1,
    )

    cli.main(["ingest-holdings", "--fund", "b"])

    assert ingested == ["b"]


def test_ingest_holdings_raises_for_unknown_fund_key(monkeypatch, fake_settings):
    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [make_fund("a")])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

    with pytest.raises(ValueError, match="unknown_key"):
        cli.main(["ingest-holdings", "--fund", "unknown_key"])


def test_ingest_holdings_prints_scrape_start_and_summary(monkeypatch, fake_settings, capsys):
    funds = [make_fund("a"), make_fund("c")]

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "ingest_holdings", lambda conn, fund, today: 5)

    cli.main(["ingest-holdings"])

    out = capsys.readouterr().out
    assert "scraping a" in out
    assert "scraping c" in out
    assert "complete: 10" in out  # 5 rows per fund, 2 funds
    assert "2 fund" in out


def test_ingest_ohlcv_runs_for_all_enabled_funds_with_default_lookback(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

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
        return 3

    monkeypatch.setattr(cli, "ingest_daily_ohlcv", fake_ingest)

    exit_code = cli.main(["ingest-ohlcv"])

    assert exit_code == 0
    assert len(calls) == 1
    passed_funds, tracked_tickers, exchange_suffixes, start_date, end_date, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert tracked_tickers == []  # tracked_tickers.yaml doesn't exist in fake_settings
    assert exchange_suffixes == {}
    assert only_tickers is None
    assert end_date == date.today()
    assert (end_date - start_date).days == 7


def test_ingest_ohlcv_respects_days_flag(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_daily_ohlcv",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            (s, e)
        )
        or 1,
    )

    cli.main(["ingest-ohlcv", "--days", "30"])

    start_date, end_date = calls[0]
    assert (end_date - start_date).days == 30


def test_ingest_ohlcv_ticker_flag_passes_only_tickers_filter(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_daily_ohlcv",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            (funds, only_tickers)
        )
        or 0,
    )

    cli.main(["ingest-ohlcv", "--ticker", "BABA", "--ticker", "0700.HK"])

    passed_funds, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert only_tickers == {"BABA", "0700.HK"}


def test_ingest_ohlcv_without_ticker_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_daily_ohlcv",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            only_tickers
        )
        or 0,
    )

    cli.main(["ingest-ohlcv"])

    assert calls[0] is None


def test_ingest_ohlcv_includes_enabled_tracked_tickers(monkeypatch, fake_settings, tmp_path):
    settings = Settings(
        database_url=fake_settings.database_url,
        config_path=fake_settings.config_path,
        tracked_tickers_path=tmp_path / "tracked_tickers.yaml",
        exchange_suffixes_path=fake_settings.exchange_suffixes_path,
    )
    (tmp_path / "tracked_tickers.yaml").write_text("tickers: []\n")  # just needs to exist
    tracked = [make_tracked_ticker("IWDA.AS"), make_tracked_ticker("BTC-USD", enabled=False)]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "load_tracked_tickers", lambda path: tracked)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_daily_ohlcv",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(tt)
        or 1,
    )

    cli.main(["ingest-ohlcv"])

    assert [t.ticker for t in calls[0]] == ["IWDA.AS"]  # disabled BTC-USD excluded


def test_ingest_ohlcv_treats_missing_tracked_tickers_file_as_empty(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_daily_ohlcv",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(tt)
        or 0,
    )

    cli.main(["ingest-ohlcv"])

    assert calls[0] == []


def test_ingest_ohlcv_loads_and_passes_exchange_suffixes(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli, "load_exchange_suffixes", lambda path: {"NASDAQ": None, "London Stock Exchange": ".L"}
    )
    monkeypatch.setattr(
        cli,
        "ingest_daily_ohlcv",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(ex)
        or 0,
    )

    cli.main(["ingest-ohlcv"])

    assert calls[0] == {"NASDAQ": None, "London Stock Exchange": ".L"}


def test_ingest_ohlcv_loads_and_passes_ticker_overrides(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "load_ticker_overrides", lambda path: {"BRKB": "BRK-B"})
    monkeypatch.setattr(
        cli,
        "ingest_daily_ohlcv",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            ticker_overrides
        )
        or 0,
    )

    cli.main(["ingest-ohlcv"])

    assert calls[0] == {"BRKB": "BRK-B"}


def test_ingest_ohlcv_propagates_config_error_from_exchange_suffixes(monkeypatch, fake_settings):
    from data_store.config_loader import ConfigError

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

    def raise_config_error(path):
        raise ConfigError("bad exchange suffixes config")

    monkeypatch.setattr(cli, "load_exchange_suffixes", raise_config_error)

    with pytest.raises(ConfigError, match="bad exchange suffixes config"):
        cli.main(["ingest-ohlcv"])


def test_backfill_ohlcv_default_run(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

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
        return 3

    monkeypatch.setattr(cli, "backfill_historical_ohlcv", fake_backfill)

    exit_code = cli.main(["backfill-ohlcv"])

    assert exit_code == 0
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


def test_backfill_ohlcv_respects_fund_flag(monkeypatch, fake_settings):
    funds = [make_fund("a"), make_fund("b")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(funds) or 0,
    )

    cli.main(["backfill-ohlcv", "--fund", "b"])

    assert [f.key for f in calls[0]] == ["b"]


def test_backfill_ohlcv_respects_earliest_date_and_delay_flags(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append((ed, ds)) or 0,
    )

    cli.main(["backfill-ohlcv", "--earliest-date", "2000-06-15", "--delay-seconds", "3.5"])

    earliest_date, delay_seconds = calls[0]
    assert earliest_date == date(2000, 6, 15)
    assert delay_seconds == 3.5


def test_backfill_ohlcv_ticker_flag_passes_only_tickers_filter(monkeypatch, fake_settings):
    # --ticker no longer pre-filters tracked_tickers.yaml and empties funds -- it passes an
    # only_tickers filter through to backfill_historical_ohlcv, applied *after* suffix
    # resolution, so it can target holdings-derived tickers (e.g. "0700.HK") too, not just
    # tracked_tickers entries. funds/tracked_tickers loading stays untouched.
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ex, ed, end, ds, only_tickers=None, **kw: calls.append(
            (funds, only_tickers)
        )
        or 0,
    )

    cli.main(["backfill-ohlcv", "--ticker", "BABA", "--ticker", "0700.HK"])

    passed_funds, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert only_tickers == {"BABA", "0700.HK"}


def test_backfill_ohlcv_without_ticker_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ex, ed, end, ds, only_tickers=None, **kw: calls.append(only_tickers)
        or 0,
    )

    cli.main(["backfill-ohlcv"])

    assert calls[0] is None


def test_backfill_ohlcv_treats_missing_tracked_tickers_file_as_empty(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(tt) or 0,
    )

    cli.main(["backfill-ohlcv"])

    assert calls[0] == []


def test_backfill_ohlcv_loads_and_passes_exchange_suffixes(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "load_exchange_suffixes", lambda path: {"NASDAQ": None})
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(ex) or 0,
    )

    cli.main(["backfill-ohlcv"])

    assert calls[0] == {"NASDAQ": None}


def test_backfill_ohlcv_loads_and_passes_ticker_overrides(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "load_ticker_overrides", lambda path: {"BRKB": "BRK-B"})
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ex, ed, end, ds, ticker_overrides=None, **kw: calls.append(
            ticker_overrides
        )
        or 0,
    )

    cli.main(["backfill-ohlcv"])

    assert calls[0] == {"BRKB": "BRK-B"}


def test_backfill_ohlcv_propagates_config_error_from_exchange_suffixes(monkeypatch, fake_settings):
    from data_store.config_loader import ConfigError

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

    def raise_config_error(path):
        raise ConfigError("bad exchange suffixes config")

    monkeypatch.setattr(cli, "load_exchange_suffixes", raise_config_error)

    with pytest.raises(ConfigError, match="bad exchange suffixes config"):
        cli.main(["backfill-ohlcv"])


def test_ingest_dividends_runs_for_all_enabled_funds_with_default_lookback(
    monkeypatch, fake_settings
):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

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
        return 3

    monkeypatch.setattr(cli, "ingest_dividend_events", fake_ingest)

    exit_code = cli.main(["ingest-dividends"])

    assert exit_code == 0
    assert len(calls) == 1
    passed_funds, tracked_tickers, exchange_suffixes, start_date, end_date, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert tracked_tickers == []
    assert exchange_suffixes == {}
    assert only_tickers is None
    assert end_date == date.today()
    assert (end_date - start_date).days == 7


def test_ingest_dividends_respects_days_flag(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_dividend_events",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            (s, e)
        )
        or 1,
    )

    cli.main(["ingest-dividends", "--days", "30"])

    start_date, end_date = calls[0]
    assert (end_date - start_date).days == 30


def test_ingest_dividends_ticker_flag_passes_only_tickers_filter(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_dividend_events",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            (funds, only_tickers)
        )
        or 0,
    )

    cli.main(["ingest-dividends", "--ticker", "CSU.TO", "--ticker", "BT-A.L"])

    passed_funds, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert only_tickers == {"CSU.TO", "BT-A.L"}


def test_ingest_dividends_without_ticker_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_dividend_events",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            only_tickers
        )
        or 0,
    )

    cli.main(["ingest-dividends"])

    assert calls[0] is None


def test_ingest_dividends_treats_missing_tracked_tickers_file_as_empty(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "ingest_dividend_events",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(tt)
        or 0,
    )

    cli.main(["ingest-dividends"])

    assert calls[0] == []


def test_ingest_dividends_loads_and_passes_exchange_suffixes(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "load_exchange_suffixes", lambda path: {"NASDAQ": None})
    monkeypatch.setattr(
        cli,
        "ingest_dividend_events",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(ex)
        or 0,
    )

    cli.main(["ingest-dividends"])

    assert calls[0] == {"NASDAQ": None}


def test_ingest_dividends_loads_and_passes_ticker_overrides(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "load_ticker_overrides", lambda path: {"BRKB": "BRK-B"})
    monkeypatch.setattr(
        cli,
        "ingest_dividend_events",
        lambda conn, funds, tt, ex, s, e, only_tickers=None, ticker_overrides=None: calls.append(
            ticker_overrides
        )
        or 0,
    )

    cli.main(["ingest-dividends"])

    assert calls[0] == {"BRKB": "BRK-B"}


def test_ingest_dividends_propagates_config_error_from_exchange_suffixes(
    monkeypatch, fake_settings
):
    from data_store.config_loader import ConfigError

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

    def raise_config_error(path):
        raise ConfigError("bad exchange suffixes config")

    monkeypatch.setattr(cli, "load_exchange_suffixes", raise_config_error)

    with pytest.raises(ConfigError, match="bad exchange suffixes config"):
        cli.main(["ingest-dividends"])


def test_backfill_dividends_default_run(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

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
        return 3

    monkeypatch.setattr(cli, "backfill_dividend_events", fake_backfill)

    exit_code = cli.main(["backfill-dividends"])

    assert exit_code == 0
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


def test_backfill_dividends_respects_fund_flag(monkeypatch, fake_settings):
    funds = [make_fund("a"), make_fund("b")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_dividend_events",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(funds) or 0,
    )

    cli.main(["backfill-dividends", "--fund", "b"])

    assert [f.key for f in calls[0]] == ["b"]


def test_backfill_dividends_respects_earliest_date_and_delay_flags(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_dividend_events",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append((ed, ds)) or 0,
    )

    cli.main(["backfill-dividends", "--earliest-date", "2000-06-15", "--delay-seconds", "3.5"])

    earliest_date, delay_seconds = calls[0]
    assert earliest_date == date(2000, 6, 15)
    assert delay_seconds == 3.5


def test_backfill_dividends_ticker_flag_passes_only_tickers_filter(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_dividend_events",
        lambda conn, funds, tt, ex, ed, end, ds, only_tickers=None, **kw: calls.append(
            (funds, only_tickers)
        )
        or 0,
    )

    cli.main(["backfill-dividends", "--ticker", "CSU.TO", "--ticker", "BT-A.L"])

    passed_funds, only_tickers = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert only_tickers == {"CSU.TO", "BT-A.L"}


def test_backfill_dividends_without_ticker_flag_passes_none(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_dividend_events",
        lambda conn, funds, tt, ex, ed, end, ds, only_tickers=None, **kw: calls.append(only_tickers)
        or 0,
    )

    cli.main(["backfill-dividends"])

    assert calls[0] is None


def test_backfill_dividends_treats_missing_tracked_tickers_file_as_empty(
    monkeypatch, fake_settings
):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_dividend_events",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(tt) or 0,
    )

    cli.main(["backfill-dividends"])

    assert calls[0] == []


def test_backfill_dividends_loads_and_passes_exchange_suffixes(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "load_exchange_suffixes", lambda path: {"NASDAQ": None})
    monkeypatch.setattr(
        cli,
        "backfill_dividend_events",
        lambda conn, funds, tt, ex, ed, end, ds, **kw: calls.append(ex) or 0,
    )

    cli.main(["backfill-dividends"])

    assert calls[0] == {"NASDAQ": None}


def test_backfill_dividends_loads_and_passes_ticker_overrides(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "load_ticker_overrides", lambda path: {"BRKB": "BRK-B"})
    monkeypatch.setattr(
        cli,
        "backfill_dividend_events",
        lambda conn, funds, tt, ex, ed, end, ds, ticker_overrides=None, **kw: calls.append(
            ticker_overrides
        )
        or 0,
    )

    cli.main(["backfill-dividends"])

    assert calls[0] == {"BRKB": "BRK-B"}


def test_backfill_dividends_propagates_config_error_from_exchange_suffixes(
    monkeypatch, fake_settings
):
    from data_store.config_loader import ConfigError

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

    def raise_config_error(path):
        raise ConfigError("bad exchange suffixes config")

    monkeypatch.setattr(cli, "load_exchange_suffixes", raise_config_error)

    with pytest.raises(ConfigError, match="bad exchange suffixes config"):
        cli.main(["backfill-dividends"])


def test_ingest_holdings_continues_after_one_fund_fails(monkeypatch, fake_settings, capsys):
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

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "ingest_holdings", flaky_ingest)

    exit_code = cli.main(["ingest-holdings"])

    assert ingested == ["a", "c"]
    out = capsys.readouterr().out
    assert "complete: 10" in out
    assert "1 fund(s) failed" in out
    # A run with a failure in it must not report success.
    assert exit_code != 0


def test_ingest_holdings_returns_zero_when_every_fund_succeeds(monkeypatch, fake_settings):
    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [make_fund("a")])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(cli, "ingest_holdings", lambda conn, fund, today: 5)

    assert cli.main(["ingest-holdings"]) == 0
