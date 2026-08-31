import logging

from portfolio import jobs

_FAKE_DATABASE_URL = "postgresql://portfolio:pw@localhost/app"  # pragma: allowlist secret
_FAKE_ADMIN_DATABASE_URL = "postgresql://admin:pw@localhost/app"  # pragma: allowlist secret


def _fake_settings():
    return type(
        "S",
        (),
        {"database_url": _FAKE_DATABASE_URL, "admin_database_url": _FAKE_ADMIN_DATABASE_URL},
    )()


class _FakeCursor:
    def __init__(self, existing_tables):
        self._existing_tables = existing_tables
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchall(self):
        return [(name,) for name in self._existing_tables]

    def fetchone(self):
        return None

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

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _patch_common(monkeypatch, tmp_path, existing_tables):
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("CREATE TABLE IF NOT EXISTS accounts (id INT);")
    monkeypatch.setattr(jobs, "SCHEMA_PATH", schema_path)
    monkeypatch.setattr(jobs, "_TARGET_TABLES", ("accounts", "transactions"))
    monkeypatch.setattr(jobs, "load_settings", _fake_settings)
    # _ensure_role_and_schema runs real DDL against Postgres -- covered by an integration
    # test, not faked cursors here. These tests are about run_db_init's own orchestration.
    monkeypatch.setattr(jobs, "_ensure_role_and_schema", lambda admin_conn, role, password: None)
    fake_conn = _FakeConnection(existing_tables)
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


def test_run_db_init_reports_ok_and_closes_connection_when_tables_are_created(
    monkeypatch, tmp_path, caplog
):
    caplog.set_level(logging.INFO)
    fake_conn = _patch_common(monkeypatch, tmp_path, existing_tables=[])

    result = jobs.run_db_init()

    assert result == {"status": "ok", "rows_saved": 2}
    assert fake_conn.closed is True
    assert "applying" in caplog.text
    assert "schema applied" in caplog.text


def test_run_db_init_reports_nothing_to_do_when_tables_already_exist(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path, existing_tables=["accounts", "transactions"])

    result = jobs.run_db_init()

    assert result == {"status": "nothing_to_do", "rows_saved": 0}


def test_run_db_init_counts_only_the_tables_that_were_missing(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path, existing_tables=["accounts"])

    result = jobs.run_db_init()

    assert result == {"status": "ok", "rows_saved": 1}


def test_run_db_init_closes_the_connection_when_applying_the_schema_fails(monkeypatch, tmp_path):
    fake_conn = _patch_common(monkeypatch, tmp_path, existing_tables=[])

    def boom(conn, path):
        raise RuntimeError("bad DDL")

    monkeypatch.setattr(jobs, "apply_schema", boom)

    try:
        jobs.run_db_init()
    except RuntimeError:
        pass

    assert fake_conn.closed is True


def test_run_db_init_logs_start_before_applying_schema(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    _patch_common(monkeypatch, tmp_path, existing_tables=[])

    jobs.run_db_init()

    messages = [record.message for record in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "applying" in m)
    done_index = next(i for i, m in enumerate(messages) if "schema applied" in m)
    assert start_index < done_index


def test_run_db_init_bootstraps_the_role_parsed_from_the_app_url(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path, existing_tables=[])
    calls = []
    monkeypatch.setattr(
        jobs,
        "_ensure_role_and_schema",
        lambda admin_conn, role, password: calls.append((role, password)),
    )

    jobs.run_db_init()

    assert calls == [("portfolio", "pw")]


def test_run_db_init_closes_the_admin_connection(monkeypatch, tmp_path):
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("CREATE TABLE IF NOT EXISTS accounts (id INT);")
    monkeypatch.setattr(jobs, "SCHEMA_PATH", schema_path)
    monkeypatch.setattr(jobs, "_TARGET_TABLES", ("accounts", "transactions"))
    monkeypatch.setattr(jobs, "load_settings", _fake_settings)
    monkeypatch.setattr(jobs, "_ensure_role_and_schema", lambda admin_conn, role, password: None)

    connections = {}

    def fake_get_connection(url):
        conn = _FakeConnection(existing_tables=[])
        connections[url] = conn
        return conn

    monkeypatch.setattr(jobs, "get_connection", fake_get_connection)

    jobs.run_db_init()

    assert connections[_FAKE_ADMIN_DATABASE_URL].closed is True
    assert connections[_FAKE_DATABASE_URL].closed is True


class _RoleCheckCursor(_FakeCursor):
    def __init__(self, role_exists):
        super().__init__(existing_tables=[])
        self._role_exists = role_exists

    def fetchone(self):
        return (1,) if self._role_exists else None


class _RoleCheckConnection(_FakeConnection):
    """Records every cursor it hands out, so a test can inspect what each one executed."""

    def __init__(self, role_exists):
        super().__init__(existing_tables=[])
        self._role_exists = role_exists
        self.cursors = []

    def cursor(self):
        cur = _RoleCheckCursor(self._role_exists)
        self.cursors.append(cur)
        return cur


def test_ensure_role_and_schema_creates_role_when_missing_then_the_schema():
    admin_conn = _RoleCheckConnection(role_exists=False)

    jobs._ensure_role_and_schema(admin_conn, "portfolio", "secret")

    statements = [str(s) for s in admin_conn.cursors[0].executed]
    assert any("CREATE ROLE" in s for s in statements)
    assert any("CREATE SCHEMA" in s and "AUTHORIZATION" in s for s in statements)
    assert admin_conn.committed is True


def test_ensure_role_and_schema_skips_create_role_when_role_already_exists():
    admin_conn = _RoleCheckConnection(role_exists=True)

    jobs._ensure_role_and_schema(admin_conn, "portfolio", "secret")

    statements = [str(s) for s in admin_conn.cursors[0].executed]
    assert not any("CREATE ROLE" in s for s in statements)
    assert any("CREATE SCHEMA" in s for s in statements)
    assert admin_conn.committed is True
