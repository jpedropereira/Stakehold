"""CLI tests: that argv maps to the right job call, and a result maps to the right exit code."""

import logging

import pytest
from auth import cli


def test_db_init_dispatches_to_the_db_init_job(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    called = {}

    def fake_run_db_init():
        called["run_db_init"] = True
        return {"status": "ok", "rows_saved": 0}

    monkeypatch.setattr(cli.jobs, "run_db_init", fake_run_db_init)

    assert cli.main(["db", "init"]) == 0
    assert called == {"run_db_init": True}


def test_db_init_prints_the_row_count_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(cli.jobs, "run_db_init", lambda: {"status": "ok", "rows_saved": 2})

    cli.main(["db", "init"])

    assert capsys.readouterr().out == "2 table(s) created\n"


def test_db_init_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs, "run_db_init", lambda: {"status": "nothing_to_do", "rows_saved": 0}
    )

    assert cli.main(["db", "init"]) == 0


def test_db_init_exits_nonzero_on_unknown_status(monkeypatch):
    monkeypatch.setattr(cli.jobs, "run_db_init", lambda: {"status": "failed", "rows_saved": 0})

    assert cli.main(["db", "init"]) == 1


def test_missing_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code != 0


def test_missing_db_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["db"])

    assert exc_info.value.code != 0
