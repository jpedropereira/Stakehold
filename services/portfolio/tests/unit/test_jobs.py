import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio import jobs
from portfolio.accounts.repository import Account
from portfolio.errors import AccountNotFound, BrokerMismatch
from portfolio.settings import SettingsError
from portfolio.ticker_mapping.loader import TickerMappings

_FAKE_DATABASE_URL = "postgresql://portfolio:pw@localhost/app"  # pragma: allowlist secret
_FAKE_ADMIN_DATABASE_URL = "postgresql://admin:pw@localhost/app"  # pragma: allowlist secret


def _fake_settings(*, datastore_api_url=None, internal_api_secret=None):
    return type(
        "S",
        (),
        {
            "database_url": _FAKE_DATABASE_URL,
            "admin_database_url": _FAKE_ADMIN_DATABASE_URL,
            "ticker_mappings_path": Path("configs/ticker_mappings.yaml"),
            "datastore_api_url": datastore_api_url,
            "internal_api_secret": internal_api_secret,
        },
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


class _SimpleFakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_new_job_common(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _fake_settings)
    fake_conn = _SimpleFakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


# --- run_add_account ---


def test_run_add_account_reports_ok_and_closes_connection_when_created(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "add_account", lambda conn, **kw: 1)

    result = jobs.run_add_account(user_id=uuid.uuid4(), broker="acme", account_label="m")

    assert result == {"status": "ok", "rows_saved": 1, "accounts_created": 1}
    assert fake_conn.closed is True


def test_run_add_account_reports_nothing_to_do_when_already_exists(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "add_account", lambda conn, **kw: 0)

    result = jobs.run_add_account(user_id=uuid.uuid4(), broker="acme", account_label="m")

    assert result == {"status": "nothing_to_do", "rows_saved": 0, "accounts_created": 0}


def test_run_add_account_reports_partial_and_closes_connection_on_domain_error(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "add_account", boom)

    result = jobs.run_add_account(user_id=uuid.uuid4(), broker="nope", account_label="m")

    assert result == {"status": "partial", "rows_saved": 0, "accounts_created": 0}
    assert fake_conn.closed is True


def test_run_add_account_logs_start_before_completion(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "add_account", lambda conn, **kw: 1)

    jobs.run_add_account(user_id=uuid.uuid4(), broker="acme", account_label="m")

    messages = [r.message for r in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "creating account" in m)
    done_index = next(i for i, m in enumerate(messages) if "done" in m)
    assert start_index < done_index


# --- run_list_accounts ---


def test_run_list_accounts_reports_ok_with_the_account_payload(monkeypatch):
    _patch_new_job_common(monkeypatch)
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    user_id = uuid.uuid4()
    accounts = [
        Account(id=1, user_id=user_id, broker="acme", account_label="m", created_at=created_at)
    ]
    monkeypatch.setattr(jobs.pipeline, "list_accounts", lambda conn, **kw: accounts)

    result = jobs.run_list_accounts(user_id=user_id)

    assert result["status"] == "ok"
    assert result["rows_saved"] == 0
    assert result["accounts_listed"] == 1
    assert result["accounts"] == [
        {"id": 1, "account_label": "m", "broker": "acme", "created_at": created_at.isoformat()}
    ]


def test_run_list_accounts_reports_nothing_to_do_when_empty(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "list_accounts", lambda conn, **kw: [])

    result = jobs.run_list_accounts(user_id=uuid.uuid4())

    assert result["status"] == "nothing_to_do"
    assert result["accounts"] == []


def test_run_list_accounts_closes_the_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "list_accounts", lambda conn, **kw: [])

    jobs.run_list_accounts(user_id=uuid.uuid4())

    assert fake_conn.closed is True


# --- run_list_available_brokers ---


def test_run_list_available_brokers_reports_ok_with_the_broker_payload(monkeypatch):
    monkeypatch.setattr(jobs.pipeline, "list_available_brokers", lambda: ["acme", "globex"])

    result = jobs.run_list_available_brokers()

    assert result["status"] == "ok"
    assert result["rows_saved"] == 0
    assert result["brokers_available"] == 2
    assert result["brokers"] == ["acme", "globex"]


def test_run_list_available_brokers_reports_nothing_to_do_when_empty(monkeypatch):
    monkeypatch.setattr(jobs.pipeline, "list_available_brokers", lambda: [])

    result = jobs.run_list_available_brokers()

    assert result["status"] == "nothing_to_do"
    assert result["brokers"] == []


def test_run_list_available_brokers_loads_no_settings_and_opens_no_connection(monkeypatch):
    """The one job with nothing to configure or connect -- BROKER_PARSERS is in-memory."""

    def boom_settings():
        raise AssertionError("run_list_available_brokers must not call load_settings")

    def boom_connection(url):
        raise AssertionError("run_list_available_brokers must not open a connection")

    monkeypatch.setattr(jobs, "load_settings", boom_settings)
    monkeypatch.setattr(jobs, "get_connection", boom_connection)
    monkeypatch.setattr(jobs.pipeline, "list_available_brokers", lambda: [])

    jobs.run_list_available_brokers()


# --- run_add_transaction ---

_TXN_KWARGS = dict(
    account_label="m",
    broker_transaction_ref="ref-1",
    transaction_type="DEPOSIT",
    trade_datetime=datetime(2026, 1, 1, tzinfo=UTC),
    native_amount=Decimal("100"),
    native_currency="EUR",
)


def test_run_add_transaction_reports_ok_when_saved(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", lambda conn, **kw: 1)

    result = jobs.run_add_transaction(user_id=uuid.uuid4(), **_TXN_KWARGS)

    assert result == {"status": "ok", "rows_saved": 1, "transactions_saved": 1}
    assert fake_conn.closed is True


def test_run_add_transaction_reports_nothing_to_do_when_ref_already_exists(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", lambda conn, **kw: 0)

    result = jobs.run_add_transaction(user_id=uuid.uuid4(), **_TXN_KWARGS)

    assert result == {"status": "nothing_to_do", "rows_saved": 0, "transactions_saved": 0}


def test_run_add_transaction_reports_partial_and_closes_connection_on_domain_error(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", boom)

    result = jobs.run_add_transaction(user_id=uuid.uuid4(), **_TXN_KWARGS)

    assert result == {"status": "partial", "rows_saved": 0, "transactions_saved": 0}
    assert fake_conn.closed is True


# --- run_import_transactions ---

_IMPORT_KWARGS = dict(account_label="m", broker="acme", file_path=Path("acme.csv"))


def test_run_import_transactions_reports_ok_when_saved(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_transactions", lambda conn, **kw: 82)

    result = jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert result == {"status": "ok", "rows_saved": 82, "transactions_saved": 82}
    assert fake_conn.closed is True


def test_run_import_transactions_reports_nothing_to_do_on_a_reimport(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_transactions", lambda conn, **kw: 0)

    result = jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert result == {"status": "nothing_to_do", "rows_saved": 0, "transactions_saved": 0}


def test_run_import_transactions_reports_partial_and_closes_connection_on_domain_error(
    monkeypatch,
):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise BrokerMismatch("nope")

    monkeypatch.setattr(jobs.pipeline, "import_transactions", boom)

    result = jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert result == {"status": "partial", "rows_saved": 0, "transactions_saved": 0}
    assert fake_conn.closed is True


def test_run_import_transactions_passes_the_file_path_through(monkeypatch):
    _patch_new_job_common(monkeypatch)
    captured = {}

    def fake_import(conn, **kw):
        captured.update(kw)
        return 1

    monkeypatch.setattr(jobs.pipeline, "import_transactions", fake_import)

    jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert captured["file_path"] == Path("acme.csv")
    assert captured["broker"] == "acme"
    assert captured["account_label"] == "m"


def test_run_import_transactions_logs_start_before_completion(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_transactions", lambda conn, **kw: 1)

    jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    messages = [r.message for r in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "importing" in m)
    done_index = next(i for i, m in enumerate(messages) if "done" in m)
    assert start_index < done_index


# --- run_list_unmapped_instruments ---


def test_run_list_unmapped_instruments_reports_ok_with_the_payload(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs, "load_ticker_mappings", lambda path: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(
        jobs.pipeline,
        "list_unmapped_instruments",
        lambda conn, **kw: [("ISIN", "IE00B4L5Y983", "acme")],
    )

    result = jobs.run_list_unmapped_instruments(user_id=uuid.uuid4())

    assert result["status"] == "ok"
    assert result["rows_saved"] == 0
    assert result["instruments_unmapped"] == 1
    assert result["unmapped_instruments"] == [
        {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
    ]


def test_run_list_unmapped_instruments_reports_nothing_to_do_when_empty(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs, "load_ticker_mappings", lambda path: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(jobs.pipeline, "list_unmapped_instruments", lambda conn, **kw: [])

    result = jobs.run_list_unmapped_instruments(user_id=uuid.uuid4())

    assert result["status"] == "nothing_to_do"
    assert result["unmapped_instruments"] == []


def test_run_list_unmapped_instruments_closes_the_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs, "load_ticker_mappings", lambda path: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(jobs.pipeline, "list_unmapped_instruments", lambda conn, **kw: [])

    jobs.run_list_unmapped_instruments(user_id=uuid.uuid4())

    assert fake_conn.closed is True


def test_run_list_unmapped_instruments_loads_mappings_from_the_settings_path(monkeypatch):
    _patch_new_job_common(monkeypatch)
    captured = {}

    def fake_load(path):
        captured["path"] = path
        return TickerMappings(isin={}, symbol={})

    monkeypatch.setattr(jobs, "load_ticker_mappings", fake_load)
    monkeypatch.setattr(jobs.pipeline, "list_unmapped_instruments", lambda conn, **kw: [])

    jobs.run_list_unmapped_instruments(user_id=uuid.uuid4())

    assert captured["path"] == Path("configs/ticker_mappings.yaml")


# --- run_import_acme_cashflows ---

_CASHFLOW_KWARGS = dict(account_label="m", file_path=Path("acme_account.csv"))


def _patch_cashflow_job_common(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(
            datastore_api_url="http://datastore:8000", internal_api_secret="secret"
        ),
    )
    fake_conn = _SimpleFakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


def test_run_import_acme_cashflows_reports_ok_when_saved(monkeypatch):
    fake_conn = _patch_cashflow_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_acme_cashflows", lambda conn, **kw: 339)

    result = jobs.run_import_acme_cashflows(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert result == {"status": "ok", "rows_saved": 339, "transactions_saved": 339}
    assert fake_conn.closed is True


def test_run_import_acme_cashflows_reports_nothing_to_do_on_a_reimport(monkeypatch):
    _patch_cashflow_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_acme_cashflows", lambda conn, **kw: 0)

    result = jobs.run_import_acme_cashflows(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert result == {"status": "nothing_to_do", "rows_saved": 0, "transactions_saved": 0}


def test_run_import_acme_cashflows_reports_partial_on_domain_error(monkeypatch):
    fake_conn = _patch_cashflow_job_common(monkeypatch)

    def boom(conn, **kw):
        raise BrokerMismatch("nope")

    monkeypatch.setattr(jobs.pipeline, "import_acme_cashflows", boom)

    result = jobs.run_import_acme_cashflows(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert result == {"status": "partial", "rows_saved": 0, "transactions_saved": 0}
    assert fake_conn.closed is True


def test_run_import_acme_cashflows_requires_datastore_api_url(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=None, internal_api_secret="secret"),
    )

    with pytest.raises(SettingsError):
        jobs.run_import_acme_cashflows(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)


def test_run_import_acme_cashflows_requires_internal_api_secret(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url="http://datastore:8000", internal_api_secret=None),
    )

    with pytest.raises(SettingsError):
        jobs.run_import_acme_cashflows(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)


def test_run_import_acme_cashflows_builds_the_http_client_from_settings(monkeypatch):
    _patch_cashflow_job_common(monkeypatch)
    captured = {}

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            captured["base_url"] = base_url
            captured["internal_api_secret"] = internal_api_secret

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(jobs.pipeline, "import_acme_cashflows", lambda conn, **kw: 1)

    jobs.run_import_acme_cashflows(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert captured == {"base_url": "http://datastore:8000", "internal_api_secret": "secret"}
