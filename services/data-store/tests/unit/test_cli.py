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
    )


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


def test_ingest_ohlcv_runs_for_all_enabled_funds_with_default_lookback(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

    def fake_ingest(conn, passed_funds, tracked_tickers, start_date, end_date):
        calls.append((passed_funds, tracked_tickers, start_date, end_date))
        return 3

    monkeypatch.setattr(cli, "ingest_daily_ohlcv", fake_ingest)

    exit_code = cli.main(["ingest-ohlcv"])

    assert exit_code == 0
    assert len(calls) == 1
    passed_funds, tracked_tickers, start_date, end_date = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert tracked_tickers == []  # tracked_tickers.yaml doesn't exist in fake_settings
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
        lambda conn, funds, tt, s, e: calls.append((s, e)) or 1,
    )

    cli.main(["ingest-ohlcv", "--days", "30"])

    start_date, end_date = calls[0]
    assert (end_date - start_date).days == 30


def test_ingest_ohlcv_includes_enabled_tracked_tickers(monkeypatch, fake_settings, tmp_path):
    settings = Settings(
        database_url=fake_settings.database_url,
        config_path=fake_settings.config_path,
        tracked_tickers_path=tmp_path / "tracked_tickers.yaml",
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
        lambda conn, funds, tt, s, e: calls.append(tt) or 1,
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
        lambda conn, funds, tt, s, e: calls.append(tt) or 0,
    )

    cli.main(["ingest-ohlcv"])

    assert calls[0] == []


def test_backfill_ohlcv_default_run(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))

    def fake_backfill(conn, passed_funds, tracked_tickers, earliest_date, end_date, delay_seconds):
        calls.append((passed_funds, tracked_tickers, earliest_date, end_date, delay_seconds))
        return 3

    monkeypatch.setattr(cli, "backfill_historical_ohlcv", fake_backfill)

    exit_code = cli.main(["backfill-ohlcv"])

    assert exit_code == 0
    assert len(calls) == 1
    passed_funds, tracked_tickers, earliest_date, end_date, delay_seconds = calls[0]
    assert [f.key for f in passed_funds] == ["a"]
    assert tracked_tickers == []
    assert earliest_date == date(1900, 1, 1)
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
        lambda conn, funds, tt, ed, end, ds: calls.append(funds) or 0,
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
        lambda conn, funds, tt, ed, end, ds: calls.append((ed, ds)) or 0,
    )

    cli.main(["backfill-ohlcv", "--earliest-date", "2000-06-15", "--delay-seconds", "3.5"])

    earliest_date, delay_seconds = calls[0]
    assert earliest_date == date(2000, 6, 15)
    assert delay_seconds == 3.5


def test_backfill_ohlcv_ticker_flag_filters_tracked_tickers_and_empties_funds(
    monkeypatch, fake_settings, tmp_path
):
    settings = Settings(
        database_url=fake_settings.database_url,
        config_path=fake_settings.config_path,
        tracked_tickers_path=tmp_path / "tracked_tickers.yaml",
    )
    (tmp_path / "tracked_tickers.yaml").write_text("tickers: []\n")
    tracked = [
        make_tracked_ticker("BABA"),
        make_tracked_ticker("NVCR"),
        make_tracked_ticker("IWDA.AS"),
    ]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [make_fund("a")])
    monkeypatch.setattr(cli, "load_tracked_tickers", lambda path: tracked)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ed, end, ds: calls.append((funds, tt)) or 0,
    )

    cli.main(["backfill-ohlcv", "--ticker", "BABA", "--ticker", "NVCR"])

    passed_funds, passed_tickers = calls[0]
    assert passed_funds == []
    assert sorted(t.ticker for t in passed_tickers) == ["BABA", "NVCR"]


def test_backfill_ohlcv_treats_missing_tracked_tickers_file_as_empty(monkeypatch, fake_settings):
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: [])
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli,
        "backfill_historical_ohlcv",
        lambda conn, funds, tt, ed, end, ds: calls.append(tt) or 0,
    )

    cli.main(["backfill-ohlcv"])

    assert calls[0] == []
