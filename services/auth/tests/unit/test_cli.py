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


def test_sessions_purge_expired_dispatches_to_the_purge_job(monkeypatch):
    called = {}

    def fake_purge():
        called["run_purge_expired_sessions"] = True
        return {"status": "ok", "rows_saved": 3}

    monkeypatch.setattr(cli.jobs, "run_purge_expired_sessions", fake_purge)

    assert cli.main(["sessions", "purge-expired"]) == 0
    assert called == {"run_purge_expired_sessions": True}


def test_sessions_purge_expired_prints_the_deleted_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs, "run_purge_expired_sessions", lambda: {"status": "ok", "rows_saved": 5}
    )

    cli.main(["sessions", "purge-expired"])

    assert capsys.readouterr().out == "5 session(s) deleted\n"


def test_sessions_purge_expired_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_purge_expired_sessions",
        lambda: {"status": "nothing_to_do", "rows_saved": 0},
    )

    assert cli.main(["sessions", "purge-expired"]) == 0


def test_missing_sessions_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["sessions"])

    assert exc_info.value.code != 0


def test_serve_dispatches_to_uvicorn_with_settings(monkeypatch):
    captured = {}

    fake_settings = type(
        "S", (), {"api_host": "127.0.0.1", "api_port": 9001, "database_url": "postgresql://x"}
    )()
    fake_app = object()

    monkeypatch.setattr("auth.settings.load_settings", lambda: fake_settings)
    monkeypatch.setattr("auth.api.app.create_app", lambda settings: fake_app)

    def fake_run(app, *, host, port):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert cli.main(["serve"]) == 0
    assert captured == {"app": fake_app, "host": "127.0.0.1", "port": 9001}


def test_missing_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code != 0


def test_missing_db_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["db"])

    assert exc_info.value.code != 0


def test_user_set_admin_grants_admin(monkeypatch, capsys):
    called = {}

    def fake_set_admin(*, email, is_admin):
        called.update(email=email, is_admin=is_admin)
        return {"status": "ok", "rows_saved": 1}

    monkeypatch.setattr(cli.jobs, "run_set_admin", fake_set_admin)

    assert cli.main(["user", "set-admin", "--email", "ann@example.com"]) == 0
    assert called == {"email": "ann@example.com", "is_admin": True}
    assert capsys.readouterr().out == "admin granted to ann@example.com\n"


def test_user_set_admin_revoke_revokes_admin(monkeypatch, capsys):
    called = {}

    def fake_set_admin(*, email, is_admin):
        called.update(email=email, is_admin=is_admin)
        return {"status": "ok", "rows_saved": 1}

    monkeypatch.setattr(cli.jobs, "run_set_admin", fake_set_admin)

    assert cli.main(["user", "set-admin", "--email", "ann@example.com", "--revoke"]) == 0
    assert called == {"email": "ann@example.com", "is_admin": False}
    assert capsys.readouterr().out == "admin revoked from ann@example.com\n"


def test_user_set_admin_exits_nonzero_for_an_unknown_email(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_set_admin",
        lambda *, email, is_admin: {"status": "user_not_found", "rows_saved": 0},
    )

    assert cli.main(["user", "set-admin", "--email", "nobody@example.com"]) == 1
    assert capsys.readouterr().out == "no user with email nobody@example.com\n"


def test_user_set_admin_requires_an_email():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["user", "set-admin"])

    assert exc_info.value.code != 0
