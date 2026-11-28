"""saved_labs/repository.py: the SQL's shape. Every statement is scoped by user_id or by a lab id
the caller resolved through a user-scoped one, and nothing here commits. The SQL itself runs
against Postgres in tests/integration/test_saved_labs.py."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from portfolio.saved_labs.base import LabAsset, SavedLab
from portfolio.saved_labs.repository import (
    delete_by_name,
    insert_assets,
    insert_lab,
    list_by_user,
    replace_lab,
)

USER = uuid.uuid4()
CREATED = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
UPDATED = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, fetchone_results=(), fetchall_result=None, rowcount=0):
        self.fetchone_results = list(fetchone_results)
        self.fetchall_result = fetchall_result or []
        self.executed = []
        self.executemany_calls = []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))

    def fetchone(self):
        return self.fetchone_results.pop(0) if self.fetchone_results else None

    def fetchall(self):
        return self.fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, **cursor_kwargs):
        self.cursor_ = FakeCursor(**cursor_kwargs)
        self.committed = False

    def cursor(self):
        return self.cursor_

    def commit(self):
        self.committed = True


def test_insert_lab_does_nothing_on_a_taken_name_and_does_not_commit():
    conn = FakeConnection(fetchone_results=[(7,)])

    lab_id = insert_lab(conn, user_id=USER, name="Core")

    sql, params = conn.cursor_.executed[0]
    assert "INSERT INTO saved_labs" in sql
    assert "ON CONFLICT (user_id, name) DO NOTHING" in sql
    assert params == (USER, "Core")
    assert lab_id == 7
    assert conn.committed is False


def test_insert_lab_returns_none_when_the_name_is_taken():
    conn = FakeConnection(fetchone_results=[None])

    assert insert_lab(conn, user_id=USER, name="Core") is None


def test_replace_lab_touches_the_owners_lab_and_drops_its_assets():
    conn = FakeConnection(fetchone_results=[(7,)])

    lab_id = replace_lab(conn, user_id=USER, name="Core")

    (update_sql, update_params), (delete_sql, delete_params) = conn.cursor_.executed
    assert "UPDATE saved_labs SET updated_at = now()" in update_sql
    assert "user_id = %s AND name = %s" in update_sql
    assert update_params == (USER, "Core")
    assert "DELETE FROM saved_lab_assets WHERE saved_lab_id = %s" in delete_sql
    assert delete_params == (7,)
    assert lab_id == 7
    assert conn.committed is False


def test_replace_lab_returns_none_and_deletes_nothing_when_the_lab_is_gone():
    conn = FakeConnection(fetchone_results=[None])

    assert replace_lab(conn, user_id=USER, name="Core") is None
    assert len(conn.cursor_.executed) == 1


def test_insert_assets_numbers_each_row_by_its_place():
    conn = FakeConnection()

    insert_assets(
        conn,
        lab_id=7,
        assets=(LabAsset("VWCE.DE", Decimal("60")), LabAsset("AAPL", Decimal("40"))),
    )

    sql, rows = conn.cursor_.executemany_calls[0]
    assert "INSERT INTO saved_lab_assets" in sql
    assert rows == [(7, 0, "VWCE.DE", Decimal("60")), (7, 1, "AAPL", Decimal("40"))]


def test_list_by_user_groups_rows_into_labs_in_order():
    conn = FakeConnection(
        fetchall_result=[
            ("Core", CREATED, UPDATED, "VWCE.DE", Decimal("60.000000")),
            ("Core", CREATED, UPDATED, "AAPL", Decimal("40.000000")),
            ("Tech", CREATED, CREATED, "MSFT", Decimal("100.000000")),
        ]
    )

    labs = list_by_user(conn, USER)

    sql, params = conn.cursor_.executed[0]
    assert "WHERE l.user_id = %s" in sql
    assert "ORDER BY l.name, a.position" in sql
    assert params == (USER,)
    assert labs == [
        SavedLab(
            name="Core",
            assets=(
                LabAsset("VWCE.DE", Decimal("60.000000")),
                LabAsset("AAPL", Decimal("40.000000")),
            ),
            created_at=CREATED,
            updated_at=UPDATED,
        ),
        SavedLab(
            name="Tech",
            assets=(LabAsset("MSFT", Decimal("100.000000")),),
            created_at=CREATED,
            updated_at=CREATED,
        ),
    ]


def test_list_by_user_returns_an_empty_list_for_no_labs():
    assert list_by_user(FakeConnection(), USER) == []


def test_delete_by_name_is_scoped_to_the_owner_and_returns_the_count():
    conn = FakeConnection(rowcount=1)

    deleted = delete_by_name(conn, user_id=USER, name="Core")

    sql, params = conn.cursor_.executed[0]
    assert sql == "DELETE FROM saved_labs WHERE user_id = %s AND name = %s"
    assert params == (USER, "Core")
    assert deleted == 1
    assert conn.committed is False
