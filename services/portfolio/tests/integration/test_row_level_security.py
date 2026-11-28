"""Row-level security on portfolio's user-owned tables (TA-6), against live Postgres: the
database itself, not repository code, keeps one user's rows from another's. See
docs/schema.md's "Row-level security" section.

Rows are seeded through `pg_connect`, which connects as the test server's superuser and so
skips RLS -- it stands in for "what is in the database". Every assertion goes through the
service's own connections (`user_connection`, `cross_user_connection`, a plain `portfolio`
connection), which is what RLS applies to.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from portfolio import cli, jobs, saved_lab_jobs
from portfolio.datastore_client.base import TickerInfo
from portfolio.db.connection import cross_user_connection, user_connection
from portfolio.settings import load_settings
from shared.db.connection import get_connection

pytestmark = pytest.mark.integration

USER_A = uuid.UUID("00000000-0000-7000-8000-00000000000a")
USER_B = uuid.UUID("00000000-0000-7000-8000-00000000000b")

_USER_OWNED_TABLES = ["accounts", "transactions", "position_snapshots", "return_snapshots"]
_SAVED_LAB_TABLES = ["saved_labs", "saved_lab_assets"]


def _seed_account(cur, user_id, label):
    cur.execute(
        "INSERT INTO accounts (user_id, broker, account_label) VALUES (%s, 'manual', %s) "
        "RETURNING id",
        (user_id, label),
    )
    account_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO transactions (account_id, broker_transaction_ref, transaction_type, "
        "trade_datetime, native_currency, native_amount, eur_amount) "
        "VALUES (%s, 'ref-1', 'DEPOSIT', '2026-01-05T10:00:00Z', 'EUR', 100, 100)",
        (account_id,),
    )
    cur.execute(
        "INSERT INTO position_snapshots (account_id, canonical_ticker, as_of_date, quantity, "
        "average_cost_native, native_currency, average_cost_eur, cost_basis_eur) "
        "VALUES (%s, 'IWDA.AS', '2026-01-05', 1, 100, 'EUR', 100, 100)",
        (account_id,),
    )
    cur.execute(
        "INSERT INTO return_snapshots (account_id, canonical_ticker, price_date, quantity, "
        "market_value_native, market_value_eur, cost_basis_eur, unrealized_return_eur) "
        "VALUES (%s, 'IWDA.AS', '2026-01-05', 1, 100, 100, 100, 0)",
        (account_id,),
    )
    return account_id


@pytest.fixture
def two_users(pg_connect):
    """One account per user, each with a row in every user-owned table."""
    with pg_connect() as conn, conn.cursor() as cur:
        return {
            USER_A: _seed_account(cur, USER_A, "a-main"),
            USER_B: _seed_account(cur, USER_B, "b-main"),
        }


def _account_ids_per_table(conn):
    seen = {}
    with conn.cursor() as cur:
        for table in _USER_OWNED_TABLES:
            column = "id" if table == "accounts" else "account_id"
            cur.execute(f"SELECT DISTINCT {column} FROM {table}")
            seen[table] = {row[0] for row in cur.fetchall()}
    return seen


def test_a_user_connection_sees_only_that_users_rows_in_every_table(two_users):
    conn = user_connection(load_settings(), USER_A)
    try:
        seen = _account_ids_per_table(conn)
    finally:
        conn.close()

    assert seen == {table: {two_users[USER_A]} for table in _USER_OWNED_TABLES}


def test_a_connection_with_no_user_set_sees_no_rows(two_users):
    conn = get_connection(load_settings().database_url)
    try:
        seen = _account_ids_per_table(conn)
    finally:
        conn.close()

    assert seen == {table: set() for table in _USER_OWNED_TABLES}


def test_a_connection_with_no_user_set_cannot_insert_an_account():
    conn = get_connection(load_settings().database_url)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (user_id, broker, account_label) "
                "VALUES (%s, 'manual', 'x')",
                (USER_A,),
            )
    finally:
        conn.close()


def test_a_user_cannot_create_an_account_owned_by_another_user():
    conn = user_connection(load_settings(), USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (user_id, broker, account_label) "
                "VALUES (%s, 'manual', 'not-mine')",
                (USER_B,),
            )
    finally:
        conn.close()


def test_a_user_cannot_write_a_transaction_into_another_users_account(two_users):
    conn = user_connection(load_settings(), USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO transactions (account_id, broker_transaction_ref, "
                "transaction_type, trade_datetime, native_currency, native_amount, eur_amount) "
                "VALUES (%s, 'ref-2', 'DEPOSIT', now(), 'EUR', 1, 1)",
                (two_users[USER_B],),
            )
    finally:
        conn.close()


def test_a_user_cannot_change_or_delete_another_users_rows(two_users, pg_connect):
    conn = user_connection(load_settings(), USER_A)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE transactions SET eur_amount = 0 WHERE account_id = %s",
                (two_users[USER_B],),
            )
            updated = cur.rowcount
            cur.execute("DELETE FROM return_snapshots WHERE account_id = %s", (two_users[USER_B],))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    assert (updated, deleted) == (0, 0)
    with pg_connect() as admin, admin.cursor() as cur:
        cur.execute(
            "SELECT eur_amount FROM transactions WHERE account_id = %s", (two_users[USER_B],)
        )
        assert cur.fetchone()[0] == 100
        cur.execute(
            "SELECT count(*) FROM return_snapshots WHERE account_id = %s", (two_users[USER_B],)
        )
        assert cur.fetchone()[0] == 1


def test_the_cross_user_connection_reads_every_users_rows(two_users):
    conn = cross_user_connection(load_settings())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM accounts")
            account_ids = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    assert account_ids == set(two_users.values())


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO accounts (user_id, broker, account_label) "
        "VALUES ('00000000-0000-7000-8000-00000000000a', 'manual', 'x')",
        "UPDATE transactions SET eur_amount = 0",
        "DELETE FROM position_snapshots",
        "DELETE FROM return_snapshots",
        "TRUNCATE accounts CASCADE",
    ],
)
def test_the_cross_user_connection_cannot_write(two_users, statement):
    conn = cross_user_connection(load_settings())
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
            cur.execute(statement)
    finally:
        conn.close()


def test_the_cross_user_connection_needs_its_url(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_CROSS_USER_DATABASE_URL")
    with pytest.raises(jobs.SettingsError, match="PORTFOLIO_CROSS_USER_DATABASE_URL"):
        cross_user_connection(load_settings())


def test_db_init_twice_keeps_row_level_security_in_place(capsys):
    assert cli.main(["db", "init"]) == 0

    conn = get_connection(load_settings().database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = 'portfolio'::regnamespace AND relkind = 'r'"
            )
            flags = {name: (enabled, forced) for name, enabled, forced in cur.fetchall()}
    finally:
        conn.close()

    owned = _USER_OWNED_TABLES + _SAVED_LAB_TABLES
    assert {t: flags[t] for t in owned} == {t: (True, True) for t in owned}
    # Global, not per user: every user's rebuild reads the same mappings.
    assert flags["ticker_mappings"] == (False, False)


# --- saved labs (LF-1) --------------------------------------------------------------------------
# Per user, not per account: saved_labs carries its owner, and saved_lab_assets checks it through
# saved_labs. No cross-user command reads them, so the cross-user role isn't granted them.


def _seed_lab(cur, user_id, name):
    cur.execute(
        "INSERT INTO saved_labs (user_id, name) VALUES (%s, %s) RETURNING id", (user_id, name)
    )
    lab_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO saved_lab_assets (saved_lab_id, position, ticker, weight_pct) "
        "VALUES (%s, 0, 'VWCE.DE', 100)",
        (lab_id,),
    )
    return lab_id


@pytest.fixture
def two_labs(pg_connect):
    """One saved lab per user, each with one asset."""
    with pg_connect() as conn, conn.cursor() as cur:
        return {USER_A: _seed_lab(cur, USER_A, "a-lab"), USER_B: _seed_lab(cur, USER_B, "b-lab")}


def _lab_ids_seen(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM saved_labs")
        labs = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT DISTINCT saved_lab_id FROM saved_lab_assets")
        assets = {row[0] for row in cur.fetchall()}
    return labs, assets


def test_a_user_connection_sees_only_that_users_saved_labs_and_their_assets(two_labs):
    conn = user_connection(load_settings(), USER_A)
    try:
        seen = _lab_ids_seen(conn)
    finally:
        conn.close()

    assert seen == ({two_labs[USER_A]}, {two_labs[USER_A]})


def test_a_connection_with_no_user_set_sees_no_saved_labs(two_labs):
    conn = get_connection(load_settings().database_url)
    try:
        seen = _lab_ids_seen(conn)
    finally:
        conn.close()

    assert seen == (set(), set())


def test_a_user_cannot_save_a_lab_owned_by_another_user():
    conn = user_connection(load_settings(), USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
            cur.execute("INSERT INTO saved_labs (user_id, name) VALUES (%s, 'x')", (USER_B,))
    finally:
        conn.close()


def test_a_user_cannot_add_an_asset_to_another_users_lab(two_labs):
    conn = user_connection(load_settings(), USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO saved_lab_assets (saved_lab_id, position, ticker, weight_pct) "
                "VALUES (%s, 1, 'AAPL', 1)",
                (two_labs[USER_B],),
            )
    finally:
        conn.close()


def test_a_user_cannot_change_or_delete_another_users_saved_lab(two_labs, pg_connect):
    conn = user_connection(load_settings(), USER_A)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE saved_lab_assets SET weight_pct = 1 WHERE saved_lab_id = %s",
                (two_labs[USER_B],),
            )
            updated = cur.rowcount
            cur.execute("DELETE FROM saved_labs WHERE id = %s", (two_labs[USER_B],))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    assert (updated, deleted) == (0, 0)
    with pg_connect() as admin, admin.cursor() as cur:
        cur.execute(
            "SELECT weight_pct FROM saved_lab_assets WHERE saved_lab_id = %s",
            (two_labs[USER_B],),
        )
        assert cur.fetchone()[0] == 100


def test_the_saved_lab_jobs_work_under_row_level_security():
    saved_lab_jobs.run_save_lab(
        user_id=USER_A, name="Core", assets=[("VWCE.DE", Decimal("60"))], replace=False
    )
    saved_lab_jobs.run_save_lab(
        user_id=USER_A, name="Core", assets=[("AAPL", Decimal("40"))], replace=True
    )

    listed = saved_lab_jobs.run_list_saved_labs(user_id=USER_A)
    assert [lab["name"] for lab in listed["labs"]] == ["Core"]
    assert saved_lab_jobs.run_list_saved_labs(user_id=USER_B)["labs"] == []

    saved_lab_jobs.run_delete_saved_lab(user_id=USER_A, name="Core")
    assert saved_lab_jobs.run_list_saved_labs(user_id=USER_A)["labs"] == []


class _ReadyTickersStub:
    def __init__(self, ready_at):
        self._ready_at = ready_at

    def list_tracked_tickers(self, statuses=None):
        # Only the two fields run_refresh_ready_tickers reads.
        return [SimpleNamespace(ticker="IWDA.AS", ready_at=self._ready_at.isoformat())]

    def get_ticker_info(self, ticker):
        return TickerInfo(exists=True, has_returns=True)

    def get_price_series(self, ticker, start, end):
        return []

    def get_fx_rate_series(self, base, quote, start, end):
        return []

    def get_fx_rate(self, base, quote, on_date):
        return None


def test_refresh_ready_tickers_rebuilds_every_users_account_holding_the_ticker(
    two_users, monkeypatch
):
    monkeypatch.setenv("DATASTORE_API_URL", "http://stub-datastore")
    monkeypatch.setenv("INTERNAL_API_SECRET", "stub-secret")  # pragma: allowlist secret
    now = datetime(2026, 1, 6, 12, 0, tzinfo=UTC)
    stub = _ReadyTickersStub(ready_at=now - timedelta(minutes=1))
    monkeypatch.setattr(jobs, "HttpDataStoreClient", lambda base_url, internal_api_secret: stub)

    result = jobs.run_refresh_ready_tickers(window_minutes=10, now=now)

    assert result["accounts_rebuilt"] == 2
    assert result["accounts_failed"] == 0
