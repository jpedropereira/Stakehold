"""The ticker-mapping admin jobs (set, delete, list, unmapped across users) and the account
rebuilds each mapping change triggers.

Split out of test_jobs.py to keep it under the repo's 64KB limit. Its small helpers are
repeated here rather than shared: a sibling helper module can't be imported under the
workspace's --import-mode=importlib."""

import logging
import uuid
from datetime import UTC, datetime

import pytest
from portfolio import jobs
from portfolio.errors import TickerMappingNotFound, UnknownTicker
from portfolio.settings import SettingsError
from portfolio.ticker_mapping.base import TickerMappings
from portfolio.ticker_mapping.repository import TickerMappingRow

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
    monkeypatch.setattr(jobs, "user_connection", lambda settings, user_id: fake_conn)
    monkeypatch.setattr(jobs, "cross_user_connection", lambda settings: fake_conn)
    return fake_conn


def _patch_datastore_settings(
    monkeypatch, *, url="http://datastore:8000", secret="secret"  # pragma: allowlist secret
):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=url, internal_api_secret=secret),
    )


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


def _no_cross_user_role(settings):
    raise SettingsError("PORTFOLIO_CROSS_USER_DATABASE_URL is not set")


def test_run_set_ticker_mapping_without_the_cross_user_role_saves_nothing(monkeypatch):
    # Refused before the write: otherwise the mapping would commit and no account would rebuild.
    _patch_mapping_job(monkeypatch)
    monkeypatch.setattr(jobs, "cross_user_connection", _no_cross_user_role)
    writes = []
    monkeypatch.setattr(jobs.pipeline, "set_ticker_mapping", lambda conn, **kw: writes.append(kw))

    with pytest.raises(SettingsError, match="PORTFOLIO_CROSS_USER_DATABASE_URL"):
        jobs.run_set_ticker_mapping(**_SET_KWARGS)

    assert writes == []


def test_run_delete_ticker_mapping_without_the_cross_user_role_deletes_nothing(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(jobs, "cross_user_connection", _no_cross_user_role)
    deletes = []
    monkeypatch.setattr(
        jobs.pipeline, "delete_ticker_mapping", lambda conn, **kw: deletes.append(kw)
    )

    with pytest.raises(SettingsError, match="PORTFOLIO_CROSS_USER_DATABASE_URL"):
        jobs.run_delete_ticker_mapping(mapping_id=7, updated_by=_ADMIN)

    assert deletes == []
