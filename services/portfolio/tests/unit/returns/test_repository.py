from datetime import date
from decimal import Decimal

from portfolio.returns.calculator import ReturnSnapshot
from portfolio.returns.repository import (
    delete_snapshots,
    list_latest_snapshots,
    list_snapshots,
    list_tickers,
    replace_snapshots,
)


def _snapshot(**overrides):
    values = dict(
        account_id=1,
        canonical_ticker="IWDA.AS",
        price_date=date(2026, 1, 6),
        quantity=Decimal("10.000000"),
        market_value_native=Decimal("1000.0000"),
        market_value_eur=Decimal("1000.0000"),
        cost_basis_eur=Decimal("950.0000"),
        unrealized_return_eur=Decimal("50.0000"),
        unrealized_return_pct=Decimal("0.05263158"),
        dividends_received_eur=Decimal("12.0000"),
        total_return_eur=Decimal("62.0000"),
        total_return_pct=Decimal("0.06526316"),
    )
    values.update(overrides)
    return ReturnSnapshot(**values)


class FakeCursor:
    def __init__(self, rowcount=0):
        self.executed = []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, params_seq):
        self.executed.append((sql, list(params_seq)))

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


def _replace(conn, snapshots):
    return replace_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS", snapshots=snapshots)


def test_replace_snapshots_deletes_the_pairs_rows_before_inserting():
    """Delete-then-insert, not upsert: a rebuild whose window shrank (an earlier BUY deleted)
    must not leave rows from before the new start behind."""
    conn = FakeConnection()

    _replace(conn, [_snapshot(), _snapshot(price_date=date(2026, 1, 7))])

    executed = conn._cursor.executed
    assert len(executed) == 2  # one delete, one batched insert
    delete_sql, delete_params = executed[0]
    assert "DELETE FROM return_snapshots" in delete_sql
    assert delete_params == (1, "IWDA.AS")
    insert_sql, params_list = executed[1]
    assert "INSERT INTO return_snapshots" in insert_sql
    assert len(params_list) == 2


def test_replace_snapshots_deletes_even_with_an_empty_replacement_set():
    conn = FakeConnection()

    saved = _replace(conn, [])

    assert saved == 0
    assert len(conn._cursor.executed) == 1
    assert "DELETE FROM return_snapshots" in conn._cursor.executed[0][0]


def test_replace_snapshots_returns_the_count_inserted():
    conn = FakeConnection()

    saved = _replace(conn, [_snapshot(), _snapshot(price_date=date(2026, 1, 7))])

    assert saved == 2


def test_replace_snapshots_does_not_commit():
    conn = FakeConnection()

    _replace(conn, [_snapshot()])

    assert conn.committed is False


def test_replace_snapshots_inserts_the_snapshots_own_fields_including_dividends():
    conn = FakeConnection()
    snapshot = _snapshot()

    _replace(conn, [snapshot])

    _, params_list = conn._cursor.executed[1]
    assert params_list == [
        (
            snapshot.account_id,
            snapshot.canonical_ticker,
            snapshot.price_date,
            snapshot.quantity,
            snapshot.market_value_native,
            snapshot.market_value_eur,
            snapshot.cost_basis_eur,
            snapshot.unrealized_return_eur,
            snapshot.unrealized_return_pct,
            snapshot.dividends_received_eur,
            snapshot.total_return_eur,
            snapshot.total_return_pct,
        )
    ]


def test_replace_snapshots_handles_a_null_pct_for_a_zero_cost_basis_position():
    conn = FakeConnection()
    snapshot = _snapshot(
        cost_basis_eur=Decimal("0.0000"),
        unrealized_return_pct=None,
        total_return_pct=None,
    )

    _replace(conn, [snapshot])

    _, params_list = conn._cursor.executed[1]
    assert params_list[0][8] is None  # unrealized_return_pct
    assert params_list[0][11] is None  # total_return_pct


# --- list_tickers ---


def test_list_tickers_returns_every_ticker_with_return_rows_for_the_account():
    conn = FakeReadConnection(rows=[("IWDA.AS",), ("VUSA.AS",)])

    result = list_tickers(conn, account_id=1)

    assert result == ["IWDA.AS", "VUSA.AS"]


def test_list_tickers_scopes_by_account_id():
    conn = FakeReadConnection(rows=[])

    list_tickers(conn, account_id=7)

    sql, params = conn._cursor.executed[0]
    assert "FROM return_snapshots" in sql
    assert "WHERE account_id = %s" in sql
    assert params == (7,)


# --- delete_snapshots ---


def test_delete_snapshots_executes_a_scoped_delete():
    conn = FakeConnection(rowcount=4)

    removed = delete_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS")

    sql, params = conn._cursor.executed[0]
    assert "DELETE FROM return_snapshots" in sql
    assert params == (1, "IWDA.AS")
    assert removed == 4


def test_delete_snapshots_does_not_commit():
    conn = FakeConnection(rowcount=1)

    delete_snapshots(conn, account_id=1, canonical_ticker="IWDA.AS")

    assert conn.committed is False


def _row(**overrides):
    snapshot = _snapshot(**overrides)
    return (
        snapshot.account_id,
        snapshot.canonical_ticker,
        snapshot.price_date,
        snapshot.quantity,
        snapshot.market_value_native,
        snapshot.market_value_eur,
        snapshot.cost_basis_eur,
        snapshot.unrealized_return_eur,
        snapshot.unrealized_return_pct,
        snapshot.dividends_received_eur,
        snapshot.total_return_eur,
        snapshot.total_return_pct,
    )


# --- list_snapshots ---


def test_list_snapshots_returns_every_stored_row_as_return_snapshots():
    row = _row()
    conn = FakeReadConnection(rows=[row])

    result = list_snapshots(conn, account_id=1)

    assert result == [ReturnSnapshot(*row)]


def test_list_snapshots_scopes_by_account_ordered_by_ticker_then_date():
    conn = FakeReadConnection(rows=[])

    list_snapshots(conn, account_id=7)

    sql, params = conn._cursor.executed[0]
    assert "WHERE account_id = %s" in sql
    assert "ORDER BY canonical_ticker, price_date" in sql
    assert params == [7]


def test_list_snapshots_filters_by_canonical_ticker_when_given():
    conn = FakeReadConnection(rows=[])

    list_snapshots(conn, account_id=7, canonical_ticker="IWDA.AS")

    sql, params = conn._cursor.executed[0]
    assert "AND canonical_ticker = %s" in sql
    assert params == [7, "IWDA.AS"]


def test_list_snapshots_filters_by_date_range_when_given():
    conn = FakeReadConnection(rows=[])

    list_snapshots(conn, account_id=7, start=date(2026, 1, 1), end=date(2026, 1, 31))

    sql, params = conn._cursor.executed[0]
    assert "AND price_date >= %s" in sql
    assert "AND price_date <= %s" in sql
    assert params == [7, date(2026, 1, 1), date(2026, 1, 31)]


def test_list_snapshots_with_no_filters_only_scopes_by_account():
    conn = FakeReadConnection(rows=[])

    list_snapshots(conn, account_id=7)

    sql, params = conn._cursor.executed[0]
    assert "canonical_ticker = %s" not in sql
    assert "price_date >=" not in sql
    assert "price_date <=" not in sql
    assert params == [7]


# --- list_latest_snapshots ---


def test_list_latest_snapshots_returns_every_stored_row_as_return_snapshots():
    row = _row()
    conn = FakeReadConnection(rows=[row])

    result = list_latest_snapshots(conn, account_id=1)

    assert result == [ReturnSnapshot(*row)]


def test_list_latest_snapshots_scopes_by_account_one_row_per_ticker():
    conn = FakeReadConnection(rows=[])

    list_latest_snapshots(conn, account_id=7)

    sql, params = conn._cursor.executed[0]
    assert "DISTINCT ON (canonical_ticker)" in sql
    assert "WHERE account_id = %s" in sql
    assert "ORDER BY canonical_ticker, price_date DESC" in sql
    assert params == (7,)
