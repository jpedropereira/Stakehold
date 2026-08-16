from datetime import date

import pytest
from data_store import cli
from data_store.config_loader import FundConfig
from data_store.settings import Settings


@pytest.fixture
def fake_settings(tmp_path):
    return Settings(database_url="postgresql://user:pass@localhost/db", config_path=tmp_path)


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

    def fake_ingest(conn, fund, start_date, end_date):
        calls.append((fund.key, start_date, end_date))
        return 3

    monkeypatch.setattr(cli, "ingest_daily_ohlcv", fake_ingest)

    exit_code = cli.main(["ingest-ohlcv"])

    assert exit_code == 0
    assert len(calls) == 1
    fund_key, start_date, end_date = calls[0]
    assert fund_key == "a"
    assert end_date == date.today()
    assert (end_date - start_date).days == 7


def test_ingest_ohlcv_respects_days_flag(monkeypatch, fake_settings):
    funds = [make_fund("a")]
    calls = []

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(cli, "load_funds", lambda path: funds)
    monkeypatch.setattr(cli, "get_connection", lambda url: FakeConn([]))
    monkeypatch.setattr(
        cli, "ingest_daily_ohlcv", lambda conn, fund, s, e: calls.append((s, e)) or 1
    )

    cli.main(["ingest-ohlcv", "--days", "30"])

    start_date, end_date = calls[0]
    assert (end_date - start_date).days == 30
