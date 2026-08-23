"""CLI tests: that argv maps to the right job call, and a result maps to the right exit code.

Everything the commands actually *do* is the job layer's, and is tested in test_jobs.py against
those functions directly. What's left here is the part only the command line has: parsing, flag
defaults, repeatable flags, subcommand wiring, and the result-to-exit-code translation. The jobs
are stubbed throughout, so a change in what a job does can't quietly pass by breaking here
instead.
"""

import pytest
from data_store import cli


@pytest.fixture
def calls(monkeypatch):
    """Record the kwargs each job would have been invoked with, and run none of them."""
    recorded = {}

    def record(name):
        def stub(**kwargs):
            recorded[name] = kwargs
            return {"status": "ok", "rows_saved": 0}

        return stub

    for name in (
        "run_ingest_holdings",
        "run_ingest_market_data",
        "run_backfill_market_data",
        "run_calculate_returns",
    ):
        monkeypatch.setattr(cli.jobs, name, record(name))

    def db_init():
        recorded["run_db_init"] = {}
        return {"status": "ok", "rows_saved": 0}

    monkeypatch.setattr(cli.jobs, "run_db_init", db_init)
    return recorded


def test_db_init_dispatches_to_the_db_init_job(calls):
    assert cli.main(["db", "init"]) == 0
    assert "run_db_init" in calls


def test_missing_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code != 0


def test_missing_db_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["db"])

    assert exc_info.value.code != 0


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["not-a-command"])

    assert exc_info.value.code != 0


# --- argv -> job kwargs ----------------------------------------------------------------------


def test_ingest_holdings_passes_no_fund_by_default(calls):
    cli.main(["ingest-holdings"])

    assert calls["run_ingest_holdings"] == {"fund": None}


def test_ingest_holdings_passes_the_fund_flag(calls):
    cli.main(["ingest-holdings", "--fund", "my_fund"])

    assert calls["run_ingest_holdings"] == {"fund": "my_fund"}


def test_ingest_market_data_defaults_to_a_seven_day_window_and_no_filters(calls):
    cli.main(["ingest-market-data"])

    assert calls["run_ingest_market_data"] == {"fund": None, "days": 7, "tickers": None}


def test_ingest_market_data_passes_every_flag_through(calls):
    cli.main(
        [
            "ingest-market-data",
            "--fund",
            "f",
            "--days",
            "30",
            "--ticker",
            "AAPL",
            "--ticker",
            "0700.HK",
        ]
    )

    assert calls["run_ingest_market_data"] == {
        "fund": "f",
        "days": 30,
        "tickers": ["AAPL", "0700.HK"],
    }


def test_backfill_market_data_defaults_to_resume_mode(calls):
    cli.main(["backfill-market-data"])

    # earliest_date=None is the resume default -- see pipeline._backfill_window.
    assert calls["run_backfill_market_data"] == {
        "fund": None,
        "tickers": None,
        "earliest_date": None,
        "delay_seconds": 10.0,
        # Rewriting rows that already exist is never the default -- it would make every rerun
        # re-request every ticker's whole history.
        "force_refresh": False,
    }


def test_backfill_market_data_passes_every_flag_through(calls):
    cli.main(
        [
            "backfill-market-data",
            "--fund",
            "f",
            "--ticker",
            "AAPL",
            "--earliest-date",
            "2000-06-15",
            "--delay-seconds",
            "3.5",
            "--force-refresh",
        ]
    )

    assert calls["run_backfill_market_data"] == {
        "fund": "f",
        "tickers": ["AAPL"],
        # Handed over as the ISO string it arrived as: the job parses it, so the DAG and the
        # CLI don't each need their own copy of that conversion.
        "earliest_date": "2000-06-15",
        "delay_seconds": 3.5,
        "force_refresh": True,
    }


def test_force_refresh_is_a_flag_not_a_value(calls):
    # The repair path for a ticker left on two price scales by a split -- see
    # pipeline._refresh_split_histories.
    cli.main(["backfill-market-data", "--ticker", "AAPL", "--force-refresh"])

    assert calls["run_backfill_market_data"]["force_refresh"] is True
    assert calls["run_backfill_market_data"]["tickers"] == ["AAPL"]


def test_calculate_returns_passes_no_ticker_filter_by_default(calls):
    cli.main(["calculate-returns"])

    assert calls["run_calculate_returns"] == {"tickers": None}


def test_calculate_returns_passes_the_ticker_flag(calls):
    cli.main(["calculate-returns", "--ticker", "AAPL", "--ticker", "0700.HK"])

    assert calls["run_calculate_returns"] == {"tickers": ["AAPL", "0700.HK"]}


def test_days_flag_is_parsed_as_an_int(calls):
    cli.main(["ingest-market-data", "--days", "30"])

    assert calls["run_ingest_market_data"]["days"] == 30


def test_delay_seconds_flag_is_parsed_as_a_float(calls):
    cli.main(["backfill-market-data", "--delay-seconds", "0.5"])

    assert calls["run_backfill_market_data"]["delay_seconds"] == 0.5


# --- result -> exit code ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("ok", 0),
        # A quiet day is not a broken one: having nothing to do is a success, and the run has
        # already said so on stdout.
        ("nothing_to_do", 0),
        # A run that half-failed must not report success to whatever scheduled it.
        ("partial", 1),
    ],
)
def test_exit_code_reflects_the_run_status(monkeypatch, status, expected_code):
    monkeypatch.setattr(
        cli.jobs, "run_ingest_market_data", lambda **kw: {"status": status, "rows_saved": 3}
    )

    assert cli.main(["ingest-market-data"]) == expected_code


def test_holdings_exit_code_reflects_the_run_status(monkeypatch):
    monkeypatch.setattr(
        cli.jobs, "run_ingest_holdings", lambda **kw: {"status": "partial", "rows_saved": 5}
    )

    assert cli.main(["ingest-holdings"]) == 1


@pytest.mark.parametrize(
    ("command", "job"),
    [
        ("ingest-market-data", "run_ingest_market_data"),
        ("backfill-market-data", "run_backfill_market_data"),
    ],
)
def test_commands_report_the_row_count_they_saved(monkeypatch, capsys, command, job):
    monkeypatch.setattr(cli.jobs, job, lambda **kw: {"status": "ok", "rows_saved": 42})

    cli.main([command])

    assert "42 market data rows" in capsys.readouterr().out


def test_calculate_returns_reports_the_row_count_it_saved(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs, "run_calculate_returns", lambda **kw: {"status": "ok", "rows_saved": 42}
    )

    cli.main(["calculate-returns"])

    assert "42 return rows" in capsys.readouterr().out
