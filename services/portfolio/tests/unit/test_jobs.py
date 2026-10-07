import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio import jobs
from portfolio.accounts.repository import Account
from portfolio.errors import AccountNotFound, TickerMappingNotFound, UnknownTicker
from portfolio.pipeline import RebuildOutcome, ReturnsRebuildOutcome
from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.settings import SettingsError
from portfolio.ticker_mapping.base import TickerMappings
from portfolio.ticker_mapping.repository import TickerMappingRow
from portfolio.transactions.base import CanonicalTransaction

_FAKE_DATABASE_URL = "postgresql://portfolio:pw@localhost/app"  # pragma: allowlist secret
_FAKE_ADMIN_DATABASE_URL = "postgresql://admin:pw@localhost/app"  # pragma: allowlist secret


def _fake_settings(*, datastore_api_url=None, internal_api_secret=None):
    return type(
        "S",
        (),
        {
            "database_url": _FAKE_DATABASE_URL,
            "admin_database_url": _FAKE_ADMIN_DATABASE_URL,
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


def _stub_rebuild_positions(monkeypatch, *, status="ok"):
    """Replaces the follow-on positions rebuild every transaction write triggers, recording each
    call -- and the returns rebuild chained after it, which _stub_rebuild_returns can then
    replace with its own recorder. The rebuilds themselves have their own tests below; these
    tests are about the write jobs' orchestration of them."""
    calls = []

    def fake_rebuild(**kw):
        calls.append(kw)
        return {"status": status, "rows_saved": 0}

    monkeypatch.setattr(jobs, "run_rebuild_positions", fake_rebuild)
    _stub_rebuild_returns(monkeypatch)
    return calls


def _stub_rebuild_returns(monkeypatch, *, status="ok"):
    calls = []

    def fake_rebuild(**kw):
        calls.append(kw)
        return {"status": status, "rows_saved": 0}

    monkeypatch.setattr(jobs, "run_rebuild_returns", fake_rebuild)
    return calls


def _patch_new_job_common(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _fake_settings)
    fake_conn = _SimpleFakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


def _patch_write_job_common(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    _stub_rebuild_positions(monkeypatch)
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


def test_run_add_account_propagates_domain_error_and_closes_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "add_account", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_add_account(user_id=uuid.uuid4(), broker="nope", account_label="m")

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
    monkeypatch.setattr(
        jobs.pipeline,
        "list_available_brokers",
        lambda: [
            jobs.pipeline.AvailableBroker(broker="acme", imports=("cashflow", "transactions")),
            jobs.pipeline.AvailableBroker(broker="globex", imports=("transactions",)),
        ],
    )

    result = jobs.run_list_available_brokers()

    assert result["status"] == "ok"
    assert result["rows_saved"] == 0
    assert result["brokers_available"] == 2
    assert result["brokers"] == [
        {"broker": "acme", "imports": ["cashflow", "transactions"]},
        {"broker": "globex", "imports": ["transactions"]},
    ]


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
    fake_conn = _patch_write_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", lambda conn, **kw: 1)

    result = jobs.run_add_transaction(user_id=uuid.uuid4(), **_TXN_KWARGS)

    assert result == {
        "status": "ok",
        "rows_saved": 1,
        "transactions_saved": 1,
        "positions_rebuild": "ok",
        "returns_rebuild": "skipped",
    }
    assert fake_conn.closed is True


def test_run_add_transaction_reports_nothing_to_do_when_ref_already_exists(monkeypatch):
    _patch_write_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", lambda conn, **kw: 0)

    result = jobs.run_add_transaction(user_id=uuid.uuid4(), **_TXN_KWARGS)

    assert result == {
        "status": "nothing_to_do",
        "rows_saved": 0,
        "transactions_saved": 0,
        "positions_rebuild": "skipped",
        "returns_rebuild": "skipped",
    }


def test_run_add_transaction_propagates_domain_error_and_closes_connection(monkeypatch):
    fake_conn = _patch_write_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_add_transaction(user_id=uuid.uuid4(), **_TXN_KWARGS)

    assert fake_conn.closed is True


# --- run_import_transactions ---

_IMPORT_KWARGS = dict(account_label="m", file_path=Path("acme.csv"))


def test_run_import_transactions_reports_ok_when_saved(monkeypatch):
    fake_conn = _patch_write_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_transactions", lambda conn, **kw: 82)

    result = jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert result == {
        "status": "ok",
        "rows_saved": 82,
        "transactions_saved": 82,
        "positions_rebuild": "ok",
        "returns_rebuild": "skipped",
    }
    assert fake_conn.closed is True


def test_run_import_transactions_reports_nothing_to_do_on_a_reimport(monkeypatch):
    _patch_write_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_transactions", lambda conn, **kw: 0)

    result = jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert result == {
        "status": "nothing_to_do",
        "rows_saved": 0,
        "transactions_saved": 0,
        "positions_rebuild": "skipped",
        "returns_rebuild": "skipped",
    }


def test_run_import_transactions_propagates_domain_error_and_closes_connection(
    monkeypatch,
):
    fake_conn = _patch_write_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "import_transactions", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert fake_conn.closed is True


def test_run_import_transactions_passes_the_file_path_through(monkeypatch):
    _patch_write_job_common(monkeypatch)
    captured = {}

    def fake_import(conn, **kw):
        captured.update(kw)
        return 1

    monkeypatch.setattr(jobs.pipeline, "import_transactions", fake_import)

    jobs.run_import_transactions(user_id=uuid.uuid4(), **_IMPORT_KWARGS)

    assert captured["file_path"] == Path("acme.csv")
    assert captured["account_label"] == "m"


def test_run_import_transactions_logs_start_before_completion(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_write_job_common(monkeypatch)
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
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
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
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(jobs.pipeline, "list_unmapped_instruments", lambda conn, **kw: [])

    result = jobs.run_list_unmapped_instruments(user_id=uuid.uuid4())

    assert result["status"] == "nothing_to_do"
    assert result["unmapped_instruments"] == []


def test_run_list_unmapped_instruments_closes_the_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(jobs.pipeline, "list_unmapped_instruments", lambda conn, **kw: [])

    jobs.run_list_unmapped_instruments(user_id=uuid.uuid4())

    assert fake_conn.closed is True


# --- run_import_cashflow ---

_CASHFLOW_KWARGS = dict(account_label="m", file_path=Path("acme_account.csv"))


def _patch_cashflow_job_common(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(
            datastore_api_url="http://datastore:8000",
            internal_api_secret="secret",  # pragma: allowlist secret
        ),
    )
    fake_conn = _SimpleFakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    _stub_rebuild_positions(monkeypatch)
    return fake_conn


def test_run_import_cashflow_reports_ok_when_saved(monkeypatch):
    fake_conn = _patch_cashflow_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_cashflow", lambda conn, **kw: 339)

    result = jobs.run_import_cashflow(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert result == {
        "status": "ok",
        "rows_saved": 339,
        "transactions_saved": 339,
        "positions_rebuild": "ok",
        "returns_rebuild": "ok",
    }
    assert fake_conn.closed is True


def test_run_import_cashflow_reports_nothing_to_do_on_a_reimport(monkeypatch):
    _patch_cashflow_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "import_cashflow", lambda conn, **kw: 0)

    result = jobs.run_import_cashflow(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert result == {
        "status": "nothing_to_do",
        "rows_saved": 0,
        "transactions_saved": 0,
        "positions_rebuild": "skipped",
        "returns_rebuild": "skipped",
    }


def test_run_import_cashflow_propagates_domain_error_and_closes_connection(monkeypatch):
    fake_conn = _patch_cashflow_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "import_cashflow", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_import_cashflow(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert fake_conn.closed is True


def test_run_import_cashflow_requires_datastore_api_url(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=None, internal_api_secret="secret"),
    )

    with pytest.raises(SettingsError):
        jobs.run_import_cashflow(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)


def test_run_import_cashflow_requires_internal_api_secret(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url="http://datastore:8000", internal_api_secret=None),
    )

    with pytest.raises(SettingsError):
        jobs.run_import_cashflow(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)


def test_run_import_cashflow_builds_the_http_client_from_settings(monkeypatch):
    _patch_cashflow_job_common(monkeypatch)
    captured = {}

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            captured["base_url"] = base_url
            captured["internal_api_secret"] = internal_api_secret

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(jobs.pipeline, "import_cashflow", lambda conn, **kw: 1)

    jobs.run_import_cashflow(user_id=uuid.uuid4(), **_CASHFLOW_KWARGS)

    assert captured == {
        "base_url": "http://datastore:8000",
        "internal_api_secret": "secret",  # pragma: allowlist secret
    }


# --- run_add_transaction: TICKER rows are checked against data-store ---

_TICKER_TXN_KWARGS = dict(
    _TXN_KWARGS,
    transaction_type="BUY",
    instrument_identifier="IWDA.AS",
    instrument_identifier_kind="TICKER",
    quantity=Decimal("5"),
    price=Decimal("100"),
)


def _patch_datastore_settings(
    monkeypatch, *, url="http://datastore:8000", secret="secret"  # pragma: allowlist secret
):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=url, internal_api_secret=secret),
    )


def test_run_add_transaction_checks_a_ticker_with_a_client_built_from_settings(monkeypatch):
    _patch_write_job_common(monkeypatch)
    _patch_datastore_settings(monkeypatch)
    built = {}
    passed = {}

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            built.update(base_url=base_url, internal_api_secret=internal_api_secret)

    def fake_add(conn, **kw):
        passed.update(kw)
        return 1

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", fake_add)

    jobs.run_add_transaction(user_id=uuid.uuid4(), **_TICKER_TXN_KWARGS)

    assert built == {
        "base_url": "http://datastore:8000",
        "internal_api_secret": "secret",  # pragma: allowlist secret
    }
    assert isinstance(passed["datastore_client"], FakeHttpDataStoreClient)


@pytest.mark.parametrize(
    "url, secret", [(None, "secret"), ("http://datastore:8000", None)], ids=["no-url", "no-secret"]
)
def test_run_add_transaction_requires_data_store_settings_for_a_ticker(monkeypatch, url, secret):
    _patch_write_job_common(monkeypatch)
    _patch_datastore_settings(monkeypatch, url=url, secret=secret)
    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", lambda conn, **kw: 1)

    with pytest.raises(SettingsError):
        jobs.run_add_transaction(user_id=uuid.uuid4(), **_TICKER_TXN_KWARGS)


def test_run_add_transaction_needs_no_data_store_for_a_non_ticker_row(monkeypatch):
    """An ISIN/SYMBOL row is resolved through mappings at rebuild time, not checked here -- so
    manual entry of one keeps working on a deployment with no data-store configured."""
    _patch_write_job_common(monkeypatch)
    passed = {}

    def fake_add(conn, **kw):
        passed.update(kw)
        return 1

    monkeypatch.setattr(jobs.pipeline, "add_manual_transaction", fake_add)

    jobs.run_add_transaction(user_id=uuid.uuid4(), **_TXN_KWARGS)

    assert passed["datastore_client"] is None


# --- get_tickers ---


def test_get_tickers_lists_data_stores_tickers(monkeypatch):
    _patch_datastore_settings(monkeypatch)

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            pass

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(
        jobs.pipeline, "list_tickers", lambda *, datastore_client: ["AAPL", "IWDA.AS"]
    )

    assert jobs.get_tickers() == ["AAPL", "IWDA.AS"]


def test_get_tickers_requires_data_store_settings(monkeypatch):
    _patch_datastore_settings(monkeypatch, url=None)

    with pytest.raises(SettingsError):
        jobs.get_tickers()


# --- positions rebuild following a transaction write ---

# Each write job, the pipeline function it wraps, and the kwargs it's called with. Parametrized
# rather than repeated per job: the follow-on rebuild is the same contract for all three.
_WRITE_JOBS = [
    pytest.param(
        "run_add_transaction", "add_manual_transaction", _TXN_KWARGS, False, id="add-transaction"
    ),
    pytest.param(
        "run_import_transactions",
        "import_transactions",
        _IMPORT_KWARGS,
        False,
        id="import-transactions",
    ),
    pytest.param(
        "run_import_cashflow", "import_cashflow", _CASHFLOW_KWARGS, True, id="import-cashflow"
    ),
]


def _patch_write_job(monkeypatch, *, needs_datastore, pipeline_fn, saved, rebuild_status="ok"):
    if needs_datastore:
        _patch_cashflow_job_common(monkeypatch)
    else:
        _patch_write_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, pipeline_fn, lambda conn, **kw: saved)
    return _stub_rebuild_positions(monkeypatch, status=rebuild_status)


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_rebuilds_positions_for_the_written_account_when_rows_were_saved(
    monkeypatch, job_name, pipeline_fn, kwargs, needs_datastore
):
    calls = _patch_write_job(
        monkeypatch, needs_datastore=needs_datastore, pipeline_fn=pipeline_fn, saved=2
    )
    user_id = uuid.uuid4()

    result = getattr(jobs, job_name)(user_id=user_id, **kwargs)

    assert len(calls) == 1
    assert calls[0]["user_id"] == user_id
    assert calls[0]["account_label"] == kwargs["account_label"]
    assert result["positions_rebuild"] == "ok"


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_skips_the_positions_rebuild_when_nothing_was_saved(
    monkeypatch, caplog, job_name, pipeline_fn, kwargs, needs_datastore
):
    caplog.set_level(logging.INFO)
    calls = _patch_write_job(
        monkeypatch, needs_datastore=needs_datastore, pipeline_fn=pipeline_fn, saved=0
    )

    result = getattr(jobs, job_name)(user_id=uuid.uuid4(), **kwargs)

    assert calls == []
    assert result["positions_rebuild"] == "skipped"
    assert any("positions and returns rebuilds skipped" in r.message for r in caplog.records)


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_reports_a_partial_positions_rebuild_without_changing_its_own_status(
    monkeypatch, job_name, pipeline_fn, kwargs, needs_datastore
):
    _patch_write_job(
        monkeypatch,
        needs_datastore=needs_datastore,
        pipeline_fn=pipeline_fn,
        saved=1,
        rebuild_status="partial",
    )

    result = getattr(jobs, job_name)(user_id=uuid.uuid4(), **kwargs)

    assert result["status"] == "ok"
    assert result["positions_rebuild"] == "partial"


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_still_reports_its_saved_rows_when_the_positions_rebuild_raises(
    monkeypatch, caplog, job_name, pipeline_fn, kwargs, needs_datastore
):
    _patch_write_job(monkeypatch, needs_datastore=needs_datastore, pipeline_fn=pipeline_fn, saved=1)

    def boom(**kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(jobs, "run_rebuild_positions", boom)

    result = getattr(jobs, job_name)(user_id=uuid.uuid4(), **kwargs)

    assert result["status"] == "ok"
    assert result["transactions_saved"] == 1
    assert result["positions_rebuild"] == "failed"
    failure_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(failure_logs) == 1
    assert failure_logs[0].exc_info is not None


# --- the returns rebuild chained after a write ---


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_rebuilds_returns_for_the_written_account_after_positions(
    monkeypatch, job_name, pipeline_fn, kwargs, needs_datastore
):
    _patch_write_job(monkeypatch, needs_datastore=needs_datastore, pipeline_fn=pipeline_fn, saved=1)
    _patch_datastore_settings(monkeypatch)
    order = []
    monkeypatch.setattr(
        jobs,
        "run_rebuild_positions",
        lambda **kw: order.append("positions") or {"status": "ok", "rows_saved": 1},
    )
    returns_calls = []

    def fake_returns(**kw):
        order.append("returns")
        returns_calls.append(kw)
        return {"status": "partial", "rows_saved": 3}

    monkeypatch.setattr(jobs, "run_rebuild_returns", fake_returns)
    user_id = uuid.uuid4()

    result = getattr(jobs, job_name)(user_id=user_id, **kwargs)

    # Returns read position_snapshots, so they have to come second.
    assert order == ["positions", "returns"]
    assert returns_calls[0]["user_id"] == user_id
    assert returns_calls[0]["account_label"] == kwargs["account_label"]
    assert result["returns_rebuild"] == "partial"


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_skips_the_returns_rebuild_when_nothing_was_saved(
    monkeypatch, job_name, pipeline_fn, kwargs, needs_datastore
):
    _patch_write_job(monkeypatch, needs_datastore=needs_datastore, pipeline_fn=pipeline_fn, saved=0)
    returns_calls = _stub_rebuild_returns(monkeypatch)

    result = getattr(jobs, job_name)(user_id=uuid.uuid4(), **kwargs)

    assert result["returns_rebuild"] == "skipped"
    assert returns_calls == []


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_skips_the_returns_rebuild_when_the_positions_rebuild_raised(
    monkeypatch, job_name, pipeline_fn, kwargs, needs_datastore
):
    _patch_write_job(monkeypatch, needs_datastore=needs_datastore, pipeline_fn=pipeline_fn, saved=1)
    _patch_datastore_settings(monkeypatch)

    def boom(**kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(jobs, "run_rebuild_positions", boom)
    returns_calls = _stub_rebuild_returns(monkeypatch)

    result = getattr(jobs, job_name)(user_id=uuid.uuid4(), **kwargs)

    # Returns computed from positions that failed to rebuild would be stale.
    assert result["positions_rebuild"] == "failed"
    assert result["returns_rebuild"] == "skipped"
    assert returns_calls == []


def test_a_write_without_data_store_configured_skips_returns_and_says_how_to_run_them(
    monkeypatch, caplog
):
    _patch_write_job(monkeypatch, needs_datastore=False, pipeline_fn="import_transactions", saved=1)
    returns_calls = _stub_rebuild_returns(monkeypatch)
    user_id = uuid.uuid4()

    result = jobs.run_import_transactions(user_id=user_id, **_IMPORT_KWARGS)

    assert result["status"] == "ok"
    assert result["returns_rebuild"] == "skipped"
    assert returns_calls == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"portfolio rebuild-returns --user {user_id}" in m for m in warnings)


@pytest.mark.parametrize("job_name, pipeline_fn, kwargs, needs_datastore", _WRITE_JOBS)
def test_write_still_succeeds_when_the_returns_rebuild_raises(
    monkeypatch, caplog, job_name, pipeline_fn, kwargs, needs_datastore
):
    _patch_write_job(monkeypatch, needs_datastore=needs_datastore, pipeline_fn=pipeline_fn, saved=1)
    _patch_datastore_settings(monkeypatch)

    def boom(**kw):
        raise RuntimeError("data-store unreachable")

    monkeypatch.setattr(jobs, "run_rebuild_returns", boom)

    result = getattr(jobs, job_name)(user_id=uuid.uuid4(), **kwargs)

    assert result["status"] == "ok"
    assert result["positions_rebuild"] == "ok"
    assert result["returns_rebuild"] == "failed"
    assert any(r.exc_info for r in caplog.records if r.levelno >= logging.ERROR)


# --- run_rebuild_positions ---


def test_run_rebuild_positions_reports_ok_with_the_payload(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=3,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result == {
        "status": "ok",
        "rows_saved": 3,
        "positions_rebuilt": 1,
        "positions_removed": 0,
        "instruments_blocked": 0,
        "positions_with_anomalies": 0,
        "pairs_failed": 0,
        "blocked_instruments": [],
        "failed_pairs": [],
    }
    assert fake_conn.closed is True


def test_run_rebuild_positions_reports_nothing_to_do_with_no_accounts(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=0,
        positions_rebuilt=0,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "nothing_to_do"


def test_run_rebuild_positions_reports_ok_when_only_pruning_occurred(monkeypatch):
    """positions_removed alone must still count as work done -- a run that only pruned stale
    snapshots is not the same as a run that touched nothing (regression test: units_total used
    to omit positions_removed, so this used to report `nothing_to_do`)."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=0,
        positions_rebuilt=0,
        positions_removed=1,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "ok"


def test_run_rebuild_positions_reports_partial_with_blocked_instruments(monkeypatch):
    """An unmapped instrument must flip the run to partial -- never a quiet ok."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=1,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[("ISIN", "IE00B4L5Y983", "acme")],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["instruments_blocked"] == 1
    assert result["blocked_instruments"] == [
        {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
    ]


def test_run_rebuild_positions_reports_partial_with_failed_pairs(monkeypatch):
    """A driver-level failure saving one pair must flip the run to partial too, and surface the
    pair -- the same treatment blocked instruments already get, not a silent drop."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=0,
        positions_rebuilt=0,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[("m", "IWDA.AS", "connection reset")],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["pairs_failed"] == 1
    assert result["failed_pairs"] == [
        {"account_label": "m", "ticker": "IWDA.AS", "error": "connection reset"}
    ]


def test_run_rebuild_positions_propagates_domain_error_and_closes_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_rebuild_positions(user_id=uuid.uuid4(), account_label="nope")

    assert fake_conn.closed is True


def test_run_rebuild_positions_propagates_a_failure_loading_the_mappings(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn):
        raise RuntimeError("database went away")

    monkeypatch.setattr(jobs.pipeline, "load_ticker_mappings", boom)

    with pytest.raises(RuntimeError):
        jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert fake_conn.closed is True


def test_run_rebuild_positions_passes_the_account_label_through(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    captured = {}

    def fake_rebuild(conn, **kw):
        captured.update(kw)
        return RebuildOutcome(
            snapshots_saved=0,
            positions_rebuilt=0,
            positions_removed=0,
            blocked=[],
            anomalies=[],
            positions_with_anomalies=0,
            failed_pairs=[],
        )

    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", fake_rebuild)
    user_id = uuid.uuid4()

    jobs.run_rebuild_positions(user_id=user_id, account_label="m")

    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"


def test_run_rebuild_positions_reports_positions_with_anomalies_from_the_outcome(monkeypatch):
    """positions_with_anomalies is passed through verbatim from RebuildOutcome, not re-derived
    from len(anomalies) -- two anomaly messages for the same position must still report 1, so
    this deliberately sets a count that disagrees with the raw message count."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=1,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[],
        anomalies=[
            "IWDA.AS in account 1: traded in both 'EUR' and 'PLN'",
            "IWDA.AS in account 1: quantity went negative",
        ],
        positions_with_anomalies=1,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "ok"
    assert result["positions_with_anomalies"] == 1


def test_run_rebuild_positions_logs_start_before_completion(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=1,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    jobs.run_rebuild_positions(user_id=uuid.uuid4())

    messages = [r.message for r in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "rebuilding positions" in m)
    done_index = next(i for i, m in enumerate(messages) if "done" in m)
    assert start_index < done_index


# --- run_rebuild_returns ---


def _patch_returns_job_common(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(
            datastore_api_url="http://datastore:8000",
            internal_api_secret="secret",  # pragma: allowlist secret
        ),
    )
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    fake_conn = _SimpleFakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


def test_run_rebuild_returns_reports_ok_with_the_payload(monkeypatch):
    fake_conn = _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=5, tickers_rebuilt=1, blocked=[], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result == {
        "status": "ok",
        "rows_saved": 5,
        "tickers_rebuilt": 1,
        "tickers_blocked": 0,
        "pairs_failed": 0,
        "blocked_tickers": [],
        "failed_pairs": [],
    }
    assert fake_conn.closed is True


def test_run_rebuild_returns_reports_nothing_to_do_with_no_tickers(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=0, tickers_rebuilt=0, blocked=[], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result["status"] == "nothing_to_do"


def test_run_rebuild_returns_reports_partial_with_blocked_tickers(monkeypatch):
    """A ticker data-store doesn't track must flip the run to partial -- never a quiet ok."""
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=0, tickers_rebuilt=0, blocked=[("m", "IWDA.AS")], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["tickers_blocked"] == 1
    assert result["blocked_tickers"] == [{"account_label": "m", "ticker": "IWDA.AS"}]


def test_run_rebuild_returns_reports_partial_with_failed_pairs(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=0,
        tickers_rebuilt=0,
        blocked=[],
        failed_pairs=[("m", "IWDA.AS", "connection reset")],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["pairs_failed"] == 1
    assert result["failed_pairs"] == [
        {"account_label": "m", "ticker": "IWDA.AS", "error": "connection reset"}
    ]


def test_run_rebuild_returns_propagates_domain_error_and_closes_connection(monkeypatch):
    fake_conn = _patch_returns_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_rebuild_returns(user_id=uuid.uuid4(), account_label="nope")

    assert fake_conn.closed is True


def test_run_rebuild_returns_requires_datastore_api_url(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=None, internal_api_secret="secret"),
    )

    with pytest.raises(SettingsError):
        jobs.run_rebuild_returns(user_id=uuid.uuid4())


def test_run_rebuild_returns_requires_internal_api_secret(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url="http://datastore:8000", internal_api_secret=None),
    )

    with pytest.raises(SettingsError):
        jobs.run_rebuild_returns(user_id=uuid.uuid4())


def test_run_rebuild_returns_builds_the_http_client_from_settings(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    captured = {}

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            captured["base_url"] = base_url
            captured["internal_api_secret"] = internal_api_secret

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(
        jobs.pipeline,
        "rebuild_returns",
        lambda conn, **kw: ReturnsRebuildOutcome(
            snapshots_saved=0, tickers_rebuilt=0, blocked=[], failed_pairs=[]
        ),
    )

    jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert captured == {
        "base_url": "http://datastore:8000",
        "internal_api_secret": "secret",  # pragma: allowlist secret
    }


def test_run_rebuild_returns_passes_the_account_label_through(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    captured = {}

    def fake_rebuild(conn, **kw):
        captured.update(kw)
        return ReturnsRebuildOutcome(
            snapshots_saved=0, tickers_rebuilt=0, blocked=[], failed_pairs=[]
        )

    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", fake_rebuild)
    user_id = uuid.uuid4()

    jobs.run_rebuild_returns(user_id=user_id, account_label="m")

    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"


def test_run_rebuild_returns_logs_start_before_completion(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=1, tickers_rebuilt=1, blocked=[], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    jobs.run_rebuild_returns(user_id=uuid.uuid4())

    messages = [r.message for r in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "rebuilding returns" in m)
    done_index = next(i for i, m in enumerate(messages) if "done" in m)
    assert start_index < done_index


# --- get_transactions ---


def test_get_transactions_returns_dicts_and_closes_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    txn = CanonicalTransaction(
        account_id=1,
        broker_transaction_ref="ref-1",
        transaction_type="BUY",
        trade_datetime=datetime(2026, 1, 5, tzinfo=UTC),
        instrument_identifier="IE00B4L5Y983",
        instrument_identifier_kind="ISIN",
        quantity=Decimal("10"),
        price=Decimal("95.00"),
        native_currency="EUR",
        native_amount=Decimal("-950.00"),
        eur_amount=Decimal("-950.0000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )
    monkeypatch.setattr(jobs.pipeline, "list_transactions", lambda conn, **kw: [txn])

    result = jobs.get_transactions(user_id=uuid.uuid4(), account_label="m")

    assert result == [
        {
            "account_id": 1,
            "broker_transaction_ref": "ref-1",
            "transaction_type": "BUY",
            "trade_datetime": txn.trade_datetime,
            "instrument_identifier": "IE00B4L5Y983",
            "instrument_identifier_kind": "ISIN",
            "quantity": Decimal("10"),
            "price": Decimal("95.00"),
            "native_currency": "EUR",
            "native_amount": Decimal("-950.00"),
            "eur_amount": Decimal("-950.0000"),
            "fx_rate": None,
            "fx_rate_source": None,
            "fees_native": None,
        }
    ]
    assert fake_conn.closed is True


def test_get_transactions_propagates_account_not_found(monkeypatch):
    _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "list_transactions", boom)

    with pytest.raises(AccountNotFound):
        jobs.get_transactions(user_id=uuid.uuid4(), account_label="nope")


def test_get_transactions_passes_user_and_account_through(monkeypatch):
    _patch_new_job_common(monkeypatch)
    captured = {}

    def fake_list_transactions(conn, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(jobs.pipeline, "list_transactions", fake_list_transactions)
    user_id = uuid.uuid4()

    jobs.get_transactions(user_id=user_id, account_label="m")

    assert captured == {"user_id": user_id, "account_label": "m"}


# --- get_positions ---


def test_get_positions_returns_dicts_and_closes_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    snapshot = PositionSnapshot(
        account_id=1,
        canonical_ticker="IWDA.AS",
        as_of_date=date(2026, 1, 5),
        quantity=Decimal("10.000000"),
        average_cost_native=Decimal("95.000000"),
        native_currency="EUR",
        average_cost_eur=Decimal("95.000000"),
        cost_basis_eur=Decimal("950.0000"),
    )
    monkeypatch.setattr(jobs.pipeline, "list_positions", lambda conn, **kw: [snapshot])

    result = jobs.get_positions(user_id=uuid.uuid4(), account_label="m")

    assert result == [
        {
            "account_id": 1,
            "canonical_ticker": "IWDA.AS",
            "as_of_date": date(2026, 1, 5),
            "quantity": Decimal("10.000000"),
            "average_cost_native": Decimal("95.000000"),
            "native_currency": "EUR",
            "average_cost_eur": Decimal("95.000000"),
            "cost_basis_eur": Decimal("950.0000"),
        }
    ]
    assert fake_conn.closed is True


def test_get_positions_propagates_account_not_found(monkeypatch):
    _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "list_positions", boom)

    with pytest.raises(AccountNotFound):
        jobs.get_positions(user_id=uuid.uuid4(), account_label="nope")


# --- get_returns ---


def test_get_returns_returns_dicts_and_closes_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    snapshot = ReturnSnapshot(
        account_id=1,
        canonical_ticker="IWDA.AS",
        price_date=date(2026, 1, 6),
        quantity=Decimal("10.000000"),
        market_value_native=Decimal("1000.0000"),
        market_value_eur=Decimal("1000.0000"),
        cost_basis_eur=Decimal("950.0000"),
        unrealized_return_eur=Decimal("50.0000"),
        unrealized_return_pct=Decimal("0.05263158"),
        dividends_received_eur=Decimal("0.0000"),
        total_return_eur=Decimal("50.0000"),
        total_return_pct=Decimal("0.05263158"),
    )
    monkeypatch.setattr(jobs.pipeline, "list_returns", lambda conn, **kw: [snapshot])

    result = jobs.get_returns(user_id=uuid.uuid4(), account_label="m")

    assert result == [
        {
            "account_id": 1,
            "canonical_ticker": "IWDA.AS",
            "price_date": date(2026, 1, 6),
            "quantity": Decimal("10.000000"),
            "market_value_native": Decimal("1000.0000"),
            "market_value_eur": Decimal("1000.0000"),
            "cost_basis_eur": Decimal("950.0000"),
            "unrealized_return_eur": Decimal("50.0000"),
            "unrealized_return_pct": Decimal("0.05263158"),
            "dividends_received_eur": Decimal("0.0000"),
            "total_return_eur": Decimal("50.0000"),
            "total_return_pct": Decimal("0.05263158"),
        }
    ]
    assert fake_conn.closed is True


def test_get_returns_passes_filters_through(monkeypatch):
    _patch_new_job_common(monkeypatch)
    captured = {}

    def fake_list_returns(conn, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(jobs.pipeline, "list_returns", fake_list_returns)
    user_id = uuid.uuid4()

    jobs.get_returns(
        user_id=user_id,
        account_label="m",
        canonical_ticker="IWDA.AS",
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
    )

    assert captured == {
        "user_id": user_id,
        "account_label": "m",
        "canonical_ticker": "IWDA.AS",
        "start": date(2026, 1, 1),
        "end": date(2026, 1, 31),
    }


def test_get_returns_propagates_account_not_found(monkeypatch):
    _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "list_returns", boom)

    with pytest.raises(AccountNotFound):
        jobs.get_returns(user_id=uuid.uuid4(), account_label="nope")


# --- get_latest_returns ---


def test_get_latest_returns_returns_dicts_and_closes_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    snapshot = ReturnSnapshot(
        account_id=1,
        canonical_ticker="IWDA.AS",
        price_date=date(2026, 1, 6),
        quantity=Decimal("10.000000"),
        market_value_native=Decimal("1000.0000"),
        market_value_eur=Decimal("1000.0000"),
        cost_basis_eur=Decimal("950.0000"),
        unrealized_return_eur=Decimal("50.0000"),
        unrealized_return_pct=Decimal("0.05263158"),
        dividends_received_eur=Decimal("0.0000"),
        total_return_eur=Decimal("50.0000"),
        total_return_pct=Decimal("0.05263158"),
    )
    monkeypatch.setattr(jobs.pipeline, "list_latest_returns", lambda conn, **kw: [snapshot])

    result = jobs.get_latest_returns(user_id=uuid.uuid4(), account_label="m")

    assert result == [
        {
            "account_id": 1,
            "canonical_ticker": "IWDA.AS",
            "price_date": date(2026, 1, 6),
            "quantity": Decimal("10.000000"),
            "market_value_native": Decimal("1000.0000"),
            "market_value_eur": Decimal("1000.0000"),
            "cost_basis_eur": Decimal("950.0000"),
            "unrealized_return_eur": Decimal("50.0000"),
            "unrealized_return_pct": Decimal("0.05263158"),
            "dividends_received_eur": Decimal("0.0000"),
            "total_return_eur": Decimal("50.0000"),
            "total_return_pct": Decimal("0.05263158"),
        }
    ]
    assert fake_conn.closed is True


def test_get_latest_returns_propagates_account_not_found(monkeypatch):
    _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "list_latest_returns", boom)

    with pytest.raises(AccountNotFound):
        jobs.get_latest_returns(user_id=uuid.uuid4(), account_label="nope")


# --- ticker mappings (admin) ---

_ADMIN = uuid.UUID("00000000-0000-7000-8000-000000000001")
_MAPPING_ROW = TickerMappingRow(
    id=7,
    identifier_kind="ISIN",
    identifier="IE00B4L5Y983",
    broker=None,
    canonical_ticker="IWDA.AS",
    updated_by=_ADMIN,
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    updated_at=datetime(2026, 10, 6, tzinfo=UTC),
)
_MAPPING_DICT = {
    "id": 7,
    "identifier_kind": "ISIN",
    "identifier": "IE00B4L5Y983",
    "broker": None,
    "canonical_ticker": "IWDA.AS",
    "updated_by": str(_ADMIN),
    "created_at": "2026-10-01T00:00:00+00:00",
    "updated_at": "2026-10-06T00:00:00+00:00",
}
_SET_KWARGS = dict(
    identifier_kind="ISIN",
    identifier="IE00B4L5Y983",
    broker=None,
    canonical_ticker="IWDA.AS",
    updated_by=_ADMIN,
)


def _patch_mapping_job(monkeypatch, *, accounts=(), rebuild_status="ok"):
    """A data-store-configured job whose affected-account lookup returns `accounts`, with the
    follow-on positions rebuild stubbed out and recorded."""
    fake_conn = _patch_new_job_common(monkeypatch)
    _patch_datastore_settings(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "list_accounts_using_instrument", lambda conn, **kw: list(accounts)
    )
    rebuilds = _stub_rebuild_positions(monkeypatch, status=rebuild_status)
    return fake_conn, rebuilds


def test_run_set_ticker_mapping_saves_and_rebuilds_every_affected_account(monkeypatch):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    fake_conn, rebuilds = _patch_mapping_job(
        monkeypatch, accounts=[(user_a, "main"), (user_b, "isa")]
    )
    passed = {}

    def fake_set(conn, **kw):
        passed.update(kw)
        return _MAPPING_ROW

    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", fake_set)

    result = jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert result == {
        "status": "ok",
        "rows_saved": 1,
        "accounts_rebuilt": 2,
        "rebuilds_failed": 0,
        "returns_rebuilds_failed": 0,
        "mapping": _MAPPING_DICT,
    }
    assert passed["canonical_ticker"] == "IWDA.AS"
    assert passed["updated_by"] == _ADMIN
    assert passed["datastore_client"] is not None
    assert [(c["user_id"], c["account_label"]) for c in rebuilds] == [
        (user_a, "main"),
        (user_b, "isa"),
    ]
    assert fake_conn.closed is True


def test_run_set_ticker_mapping_looks_up_affected_accounts_by_the_saved_row(monkeypatch):
    _patch_mapping_job(monkeypatch)
    symbol_row = TickerMappingRow(
        **{**_MAPPING_ROW.__dict__, "identifier_kind": "SYMBOL", "broker": "globex"}
    )
    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", lambda conn, **kw: symbol_row)
    looked_up = {}

    def fake_lookup(conn, **kw):
        looked_up.update(kw)
        return []

    monkeypatch.setattr(jobs.pipeline, "list_accounts_using_instrument", fake_lookup)

    jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert looked_up == {
        "identifier_kind": "SYMBOL",
        "identifier": "IE00B4L5Y983",
        "broker": "globex",
    }


def test_run_set_ticker_mapping_counts_a_rebuild_that_raises_and_keeps_going(monkeypatch, caplog):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    _patch_mapping_job(monkeypatch, accounts=[(user_a, "main"), (user_b, "isa")])
    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", lambda conn, **kw: _MAPPING_ROW)
    calls = []

    def flaky_rebuild(**kw):
        calls.append(kw["account_label"])
        if kw["account_label"] == "main":
            raise RuntimeError("database went away")
        return {"status": "ok", "rows_saved": 1}

    monkeypatch.setattr(jobs, "run_rebuild_positions", flaky_rebuild)

    result = jobs.run_set_ticker_mapping(**_SET_KWARGS)

    # The mapping is saved either way; a failed follow-on rebuild is reported, not raised.
    assert result["status"] == "ok"
    assert result["accounts_rebuilt"] == 1
    assert result["rebuilds_failed"] == 1
    assert calls == ["main", "isa"]
    assert any(r.exc_info for r in caplog.records if r.levelno >= logging.ERROR)


def test_run_set_ticker_mapping_with_no_affected_accounts_still_saves(monkeypatch):
    _, rebuilds = _patch_mapping_job(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", lambda conn, **kw: _MAPPING_ROW)

    result = jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert result["status"] == "ok"
    assert result["accounts_rebuilt"] == 0
    assert rebuilds == []


def test_run_set_ticker_mapping_requires_data_store(monkeypatch):
    _patch_new_job_common(monkeypatch)

    with pytest.raises(SettingsError, match="ticker-mapping set"):
        jobs.run_set_ticker_mapping(**_SET_KWARGS)


def test_run_set_ticker_mapping_propagates_an_unknown_ticker_and_closes(monkeypatch):
    fake_conn, rebuilds = _patch_mapping_job(monkeypatch)

    def boom(conn, **kw):
        raise UnknownTicker("IWDA.AM isn't in Stakehold's market data")

    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", boom)

    with pytest.raises(UnknownTicker):
        jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert fake_conn.closed is True
    assert rebuilds == []


def test_run_set_ticker_mapping_logs_start_and_outcome(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_mapping_job(monkeypatch, accounts=[(uuid.uuid4(), "main")])
    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", lambda conn, **kw: _MAPPING_ROW)

    jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert "ticker-mapping set: mapping ISIN IE00B4L5Y983" in caplog.text
    assert "1 account(s) rebuilt, 0 failed" in caplog.text


def test_run_delete_ticker_mapping_deletes_and_rebuilds_affected_accounts(monkeypatch):
    user_a = uuid.uuid4()
    fake_conn, rebuilds = _patch_mapping_job(monkeypatch, accounts=[(user_a, "main")])
    passed = {}

    def fake_delete(conn, **kw):
        passed.update(kw)
        return _MAPPING_ROW

    monkeypatch.setattr(jobs.pipeline, "delete_ticker_mapping", fake_delete)

    result = jobs.run_delete_ticker_mapping(mapping_id=7, updated_by=_ADMIN)

    assert result == {
        "status": "ok",
        "rows_saved": 1,
        "accounts_rebuilt": 1,
        "rebuilds_failed": 0,
        "returns_rebuilds_failed": 0,
        "mapping": _MAPPING_DICT,
    }
    assert passed == {"mapping_id": 7}
    assert [c["account_label"] for c in rebuilds] == ["main"]
    assert fake_conn.closed is True


def test_run_delete_ticker_mapping_does_not_need_data_store(monkeypatch):
    # Nothing is checked against data-store when a mapping goes away.
    _patch_new_job_common(monkeypatch)
    _stub_rebuild_positions(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "delete_ticker_mapping", lambda conn, **kw: _MAPPING_ROW)
    monkeypatch.setattr(jobs.pipeline, "list_accounts_using_instrument", lambda conn, **kw: [])

    assert jobs.run_delete_ticker_mapping(mapping_id=7, updated_by=_ADMIN)["status"] == "ok"


def test_run_delete_ticker_mapping_propagates_not_found_and_closes(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise TickerMappingNotFound("no ticker mapping with id 7")

    monkeypatch.setattr(jobs.pipeline, "delete_ticker_mapping", boom)

    with pytest.raises(TickerMappingNotFound):
        jobs.run_delete_ticker_mapping(mapping_id=7, updated_by=_ADMIN)

    assert fake_conn.closed is True


def test_run_delete_ticker_mapping_logs_who_deleted_it(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_mapping_job(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "delete_ticker_mapping", lambda conn, **kw: _MAPPING_ROW)

    jobs.run_delete_ticker_mapping(mapping_id=7, updated_by=_ADMIN)

    assert f"by user {_ADMIN}" in caplog.text


def test_get_ticker_mappings_returns_every_mapping_as_dicts(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "list_ticker_mappings", lambda conn: [_MAPPING_ROW])

    assert jobs.get_ticker_mappings() == [_MAPPING_DICT]
    assert fake_conn.closed is True


def test_run_list_all_unmapped_instruments_reports_the_global_list(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(
        jobs.pipeline,
        "list_all_unmapped_instruments",
        lambda conn, **kw: [("ISIN", "IE00B4L5Y983", None), ("SYMBOL", "SGLD", "globex")],
    )

    result = jobs.run_list_all_unmapped_instruments()

    assert result["status"] == "ok"
    assert result["instruments_unmapped"] == 2
    assert result["unmapped_instruments"] == [
        {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": None},
        {"kind": "SYMBOL", "identifier": "SGLD", "broker": "globex"},
    ]
    assert fake_conn.closed is True


def test_run_list_all_unmapped_instruments_reports_nothing_to_do_when_empty(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(jobs.pipeline, "list_all_unmapped_instruments", lambda conn, **kw: [])

    assert jobs.run_list_all_unmapped_instruments()["status"] == "nothing_to_do"


def test_run_db_init_targets_the_ticker_mappings_table():
    assert "ticker_mappings" in jobs._TARGET_TABLES


def test_a_mapping_change_rebuilds_returns_for_each_affected_account(monkeypatch):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    _patch_mapping_job(monkeypatch, accounts=[(user_a, "main"), (user_b, "isa")])
    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", lambda conn, **kw: _MAPPING_ROW)
    returns_calls = _stub_rebuild_returns(monkeypatch)

    jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert [(c["user_id"], c["account_label"]) for c in returns_calls] == [
        (user_a, "main"),
        (user_b, "isa"),
    ]


def test_a_mapping_change_counts_returns_rebuilds_that_failed(monkeypatch):
    _patch_mapping_job(monkeypatch, accounts=[(uuid.uuid4(), "main"), (uuid.uuid4(), "isa")])
    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", lambda conn, **kw: _MAPPING_ROW)

    def flaky_returns(**kw):
        if kw["account_label"] == "main":
            raise RuntimeError("data-store unreachable")
        return {"status": "ok", "rows_saved": 1}

    monkeypatch.setattr(jobs, "run_rebuild_returns", flaky_returns)

    result = jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert result["accounts_rebuilt"] == 2
    assert result["rebuilds_failed"] == 0
    assert result["returns_rebuilds_failed"] == 1


# --- run_rebuild_returns_all ---


def _patch_returns_all(monkeypatch, *, user_ids, per_user):
    """`per_user` maps a user id to the result its run_rebuild_returns returns, or to an
    exception it raises."""
    fake_conn = _patch_new_job_common(monkeypatch)
    _patch_datastore_settings(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "list_user_ids", lambda conn: list(user_ids))
    calls = []

    def fake_returns(**kw):
        calls.append(kw["user_id"])
        outcome = per_user[kw["user_id"]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(jobs, "run_rebuild_returns", fake_returns)
    return fake_conn, calls


def _returns_result(status="ok", rows_saved=10, blocked=0, pairs_failed=0):
    return {
        "status": status,
        "rows_saved": rows_saved,
        "tickers_rebuilt": 2,
        "tickers_blocked": blocked,
        "pairs_failed": pairs_failed,
        "blocked_tickers": [],
        "failed_pairs": [],
    }


def test_run_rebuild_returns_all_rebuilds_every_user_and_sums_the_counts(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    ann, bea = uuid.uuid4(), uuid.uuid4()
    fake_conn, calls = _patch_returns_all(
        monkeypatch,
        user_ids=[ann, bea],
        per_user={ann: _returns_result(rows_saved=10), bea: _returns_result(rows_saved=5)},
    )

    result = jobs.run_rebuild_returns_all()

    assert calls == [ann, bea]
    assert result == {
        "status": "ok",
        "rows_saved": 15,
        "users_rebuilt": 2,
        "users_failed": 0,
        "tickers_blocked": 0,
        "pairs_failed": 0,
    }
    assert fake_conn.closed is True
    assert "rebuilding returns for 2 user(s)" in caplog.text
    assert "done, 15 return snapshot(s) saved for 2 user(s), 0 failed" in caplog.text


def test_run_rebuild_returns_all_keeps_going_when_one_user_fails(monkeypatch, caplog):
    ann, bea = uuid.uuid4(), uuid.uuid4()
    _, calls = _patch_returns_all(
        monkeypatch,
        user_ids=[ann, bea],
        per_user={ann: RuntimeError("data-store unreachable"), bea: _returns_result()},
    )

    result = jobs.run_rebuild_returns_all()

    assert calls == [ann, bea]
    assert result["status"] == "partial"
    assert result["users_rebuilt"] == 1
    assert result["users_failed"] == 1
    assert any(r.exc_info for r in caplog.records if r.levelno >= logging.ERROR)


def test_run_rebuild_returns_all_is_partial_when_a_user_had_blocked_tickers(monkeypatch):
    ann = uuid.uuid4()
    _patch_returns_all(
        monkeypatch,
        user_ids=[ann],
        per_user={ann: _returns_result(status="partial", blocked=1)},
    )

    result = jobs.run_rebuild_returns_all()

    assert result["status"] == "partial"
    assert result["tickers_blocked"] == 1
    assert result["users_failed"] == 0


def test_run_rebuild_returns_all_with_no_users_is_nothing_to_do(monkeypatch):
    _patch_returns_all(monkeypatch, user_ids=[], per_user={})

    assert jobs.run_rebuild_returns_all()["status"] == "nothing_to_do"


def test_run_rebuild_returns_all_requires_data_store(monkeypatch):
    _patch_new_job_common(monkeypatch)

    with pytest.raises(SettingsError, match="rebuild-returns"):
        jobs.run_rebuild_returns_all()
