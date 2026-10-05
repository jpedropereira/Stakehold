import logging

from auth import jobs


class _FakeCursor:
    def __init__(self, existing_tables):
        self._existing_tables = existing_tables
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchall(self):
        return [(name,) for name in self._existing_tables]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, existing_tables):
        self._existing_tables = existing_tables
        self.closed = False
        self.committed = False

    def cursor(self):
        return _FakeCursor(self._existing_tables)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _patch_common(monkeypatch, tmp_path, existing_tables):
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("CREATE TABLE IF NOT EXISTS users (id INT);")
    monkeypatch.setattr(jobs, "SCHEMA_PATH", schema_path)
    monkeypatch.setattr(jobs, "_TARGET_TABLES", ("users",))
    monkeypatch.setattr(
        jobs, "load_settings", lambda: type("S", (), {"database_url": "postgresql://x"})()
    )
    fake_conn = _FakeConnection(existing_tables)
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


def test_run_db_init_reports_ok_and_closes_connection_when_tables_are_created(
    monkeypatch, tmp_path, caplog
):
    caplog.set_level(logging.INFO)
    fake_conn = _patch_common(monkeypatch, tmp_path, existing_tables=[])

    result = jobs.run_db_init()

    assert result == {"status": "ok", "rows_saved": 1}
    assert fake_conn.closed is True
    assert "applying" in caplog.text
    assert "schema applied" in caplog.text


def test_run_db_init_reports_nothing_to_do_when_tables_already_exist(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path, existing_tables=["users"])

    result = jobs.run_db_init()

    assert result == {"status": "nothing_to_do", "rows_saved": 0}


def test_run_db_init_logs_start_before_applying_schema(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    _patch_common(monkeypatch, tmp_path, existing_tables=[])

    jobs.run_db_init()

    messages = [record.message for record in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "applying" in m)
    done_index = next(i for i, m in enumerate(messages) if "schema applied" in m)
    assert start_index < done_index


class _FakeCloseOnlyConnection:
    def __init__(self):
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _patch_purge_common(monkeypatch, deleted):
    fake_conn = _FakeCloseOnlyConnection()
    monkeypatch.setattr(
        jobs, "load_settings", lambda: type("S", (), {"database_url": "postgresql://x"})()
    )
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    monkeypatch.setattr(jobs.sessions_repository, "delete_expired", lambda conn, now: deleted)
    return fake_conn


def test_run_purge_expired_sessions_reports_ok_and_closes_connection_when_rows_deleted(
    monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    fake_conn = _patch_purge_common(monkeypatch, deleted=3)

    result = jobs.run_purge_expired_sessions()

    assert result == {"status": "ok", "rows_saved": 3}
    assert fake_conn.closed is True
    # The repositories no longer commit for themselves, so the job has to -- without this the
    # delete is rolled back when the connection closes and the purge silently does nothing.
    assert fake_conn.committed is True
    assert "deleting sessions" in caplog.text
    assert "done" in caplog.text


def test_run_purge_expired_sessions_reports_nothing_to_do_when_none_deleted(monkeypatch):
    _patch_purge_common(monkeypatch, deleted=0)

    result = jobs.run_purge_expired_sessions()

    assert result == {"status": "nothing_to_do", "rows_saved": 0}


def _patch_set_admin_common(monkeypatch, found):
    fake_conn = _FakeCloseOnlyConnection()
    calls = []
    monkeypatch.setattr(
        jobs, "load_settings", lambda: type("S", (), {"database_url": "postgresql://x"})()
    )
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)

    def fake_set_admin(conn, *, email, is_admin):
        calls.append((email, is_admin))
        return found

    monkeypatch.setattr(jobs.service, "set_admin", fake_set_admin)
    return fake_conn, calls


def test_run_set_admin_reports_ok_and_closes_connection(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    fake_conn, calls = _patch_set_admin_common(monkeypatch, found=True)

    result = jobs.run_set_admin(email="ann@example.com", is_admin=True)

    assert result == {"status": "ok", "rows_saved": 1}
    assert calls == [("ann@example.com", True)]
    assert fake_conn.closed is True
    assert "granting admin" in caplog.text
    assert "done" in caplog.text


def test_run_set_admin_reports_user_not_found_for_an_unknown_email(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    fake_conn, _ = _patch_set_admin_common(monkeypatch, found=False)

    result = jobs.run_set_admin(email="nobody@example.com", is_admin=False)

    assert result == {"status": "user_not_found", "rows_saved": 0}
    assert fake_conn.closed is True
    assert "revoking admin" in caplog.text
    assert "no user" in caplog.text


def test_run_set_admin_keeps_the_email_out_of_the_logs(monkeypatch, caplog):
    # Same rule as signup/login's own log lines: no PII in the service log.
    caplog.set_level(logging.INFO)
    _patch_set_admin_common(monkeypatch, found=True)

    jobs.run_set_admin(email="ann@example.com", is_admin=True)

    assert "ann@example.com" not in caplog.text
