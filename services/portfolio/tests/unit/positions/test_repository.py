from datetime import date

from portfolio.positions.calculator import PositionSnapshot
from portfolio.positions.repository import (
    delete_snapshots,
    list_tickers,
    replace_snapshots,
)


def _snapshot(**overrides):
    from decimal import Decimal

    values = dict(
        account_id=1,
        canonical_ticker="IWDA.AS",
        as_of_date=date(2026, 1, 5),
        quantity=Decimal("10.000000"),
        average_cost_native=Decimal("95.000000"),
        native_currency="EUR",
        average_cost_eur=Decimal("95.000000"),
        cost_basis_eur=Decimal("950.0000"),
    )
    values.update(overrides)
    return PositionSnapshot(**values)


class FakeCursor:
    def __init__(self, rowcount=0):
        self.executed = []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, rowcount=0):
        self._cursor = FakeCursor(rowcount=rowcount)
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


class FakeReadCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeReadConnection:
    def __init__(self, rows):
        self._cursor = FakeReadCursor(rows)

    def cursor(self):
        return self._cursor


# --- replace_snapshots ---


def test_replace_snapshots_deletes_before_inserting():
    conn = FakeConnection()

    replace_snapshots(
        conn, account_id=1, canonical_ticker="IWDA.AS", snapshots=[_snapshot(), _snapshot()]
    )

    executed = conn._cursor.executed
    assert "DELETE FROM position_snapshots" in executed[0][0]
    assert executed[0][1] == (1, "IWDA.AS")
    assert all("INSERT INTO position_snapshots" in sql for sql, _ in executed[1:])
    assert len(executed) == 3  # one delete, two inserts


def test_replace_snapshots_deletes_even_with_an_empty_replacement_set():
    conn = FakeConnection()

    saved = replace_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS", snapshots=[])

    assert saved == 0
    assert len(conn._cursor.executed) == 1
    assert "DELETE" in conn._cursor.executed[0][0]


def test_replace_snapshots_returns_the_count_inserted():
    conn = FakeConnection()

    saved = replace_snapshots(
        conn, account_id=1, canonical_ticker="IWDA.AS", snapshots=[_snapshot(), _snapshot()]
    )

    assert saved == 2


def test_replace_snapshots_does_not_commit():
    conn = FakeConnection()

    replace_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS", snapshots=[_snapshot()])

    assert conn.committed is False


def test_replace_snapshots_inserts_the_snapshots_own_fields():
    conn = FakeConnection()
    snapshot = _snapshot()

    replace_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS", snapshots=[snapshot])

    _, params = conn._cursor.executed[1]
    assert params == (
        snapshot.account_id,
        snapshot.canonical_ticker,
        snapshot.as_of_date,
        snapshot.quantity,
        snapshot.average_cost_native,
        snapshot.native_currency,
        snapshot.average_cost_eur,
        snapshot.cost_basis_eur,
    )


# --- list_tickers ---


def test_list_tickers_returns_every_ticker_for_the_account():
    conn = FakeReadConnection(rows=[("IWDA.AS",), ("VUSA.AS",)])

    result = list_tickers(conn, account_id=1)

    assert result == ["IWDA.AS", "VUSA.AS"]


def test_list_tickers_scopes_by_account_id():
    conn = FakeReadConnection(rows=[])

    list_tickers(conn, account_id=7)

    sql, params = conn._cursor.executed[0]
    assert "WHERE account_id = %s" in sql
    assert params == (7,)


# --- delete_snapshots ---


def test_delete_snapshots_executes_a_scoped_delete():
    conn = FakeConnection(rowcount=3)

    removed = delete_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS")

    sql, params = conn._cursor.executed[0]
    assert "DELETE FROM position_snapshots" in sql
    assert params == (1, "IWDA.AS")
    assert removed == 3


def test_delete_snapshots_does_not_commit():
    conn = FakeConnection(rowcount=1)

    delete_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS")

    assert conn.committed is False
