from datetime import date

import pytest
from data_store.regimes.loader import RegimeConfig
from data_store.regimes.repository import (
    count_regimes,
    get_overlapping_regimes,
    save_regimes,
)


class FakeCopy:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write_row(self, row):
        self.rows.append(row)


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.copy_calls = []
        self.copy_rows = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "COPY" in sql or "INSERT" in sql:
            self.rowcount = len(self.copy_rows)

    def copy(self, sql):
        self.copy_calls.append(sql)
        return FakeCopy(self.copy_rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self):
        self._cursor = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_save_regimes_deletes_and_copies():
    conn = FakeConnection()
    rows = [
        RegimeConfig(
            regime_id="gfc",
            dimension="Financial",
            scope="Global",
            name="Global Financial Crisis",
            start_date=date(2007, 9, 1),
            end_date=date(2009, 3, 1),
            known_from=date(2009, 3, 1),
            severity="extreme",
            source="Fed",
            notes="",
        )
    ]

    count = save_regimes(conn, rows)

    assert count == 1
    assert conn.committed is True
    delete_sql = conn._cursor.executed[0][0]
    assert "DELETE FROM regimes" in delete_sql
    copy_sql = conn._cursor.copy_calls[0]
    assert "COPY regimes" in copy_sql
    assert "regime_id" in copy_sql


def test_save_regimes_writes_all_columns():
    conn = FakeConnection()
    rows = [
        RegimeConfig(
            regime_id="ongoing",
            dimension="Geopolitical",
            scope="Europe",
            name="Ongoing War",
            start_date=date(2022, 2, 24),
            end_date=None,
            known_from=None,
            severity=None,
            source="Bloomberg",
            notes=None,
        )
    ]

    save_regimes(conn, rows)

    copy_rows = conn._cursor.copy_rows
    assert len(copy_rows) == 1
    assert copy_rows[0][0] == "ongoing"
    assert copy_rows[0][1] == "Geopolitical"
    assert copy_rows[0][2] == "Europe"
    assert copy_rows[0][3] == "Ongoing War"
    assert copy_rows[0][4] == date(2022, 2, 24)
    assert copy_rows[0][5] is None
    assert copy_rows[0][6] is None
    assert copy_rows[0][7] is None
    assert copy_rows[0][8] == "Bloomberg"
    assert copy_rows[0][9] is None


def test_save_regimes_writes_multiple_rows():
    conn = FakeConnection()
    rows = [
        RegimeConfig(
            regime_id="gfc",
            dimension="Financial",
            scope="Global",
            name="GFC",
            start_date=date(2007, 9, 1),
            end_date=date(2009, 3, 1),
            known_from=date(2009, 3, 1),
            severity="extreme",
            source="Fed",
            notes="Lehman collapse",
        ),
        RegimeConfig(
            regime_id="covid",
            dimension="Health",
            scope="Global",
            name="COVID-19",
            start_date=date(2020, 3, 1),
            end_date=None,
            known_from=None,
            severity="severe",
            source="WHO",
            notes=None,
        ),
    ]

    count = save_regimes(conn, rows)

    assert count == 2
    assert len(conn._cursor.copy_rows) == 2


def test_save_regimes_handles_empty_list():
    conn = FakeConnection()
    rows = []

    count = save_regimes(conn, rows)

    assert count == 0
    assert conn.committed is True


def test_save_regimes_rolls_back_on_error():
    class FailingCursor(FakeCursor):
        def copy(self, sql):
            raise RuntimeError("disk full")

    class FailingConnection(FakeConnection):
        def __init__(self):
            super().__init__()
            self._cursor = FailingCursor()

    conn = FailingConnection()
    rows = [
        RegimeConfig(
            regime_id="gfc",
            dimension="Financial",
            scope="Global",
            name="GFC",
            start_date=date(2007, 9, 1),
            end_date=date(2009, 3, 1),
            known_from=None,
            severity="extreme",
            source="Fed",
            notes=None,
        )
    ]

    with pytest.raises(RuntimeError, match="disk full"):
        save_regimes(conn, rows)

    assert conn.rolled_back is True
    assert conn.committed is False


# --- reads -------------------------------------------------------------------------------------


class ReadCursor:
    def __init__(self, *, rows=(), one=None):
        self.executed = []
        self._rows = list(rows)
        self._one = one

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ReadConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_get_overlapping_regimes_binds_the_range_as_parameters():
    row = ("gfc", "drawdown", "SPX", "GFC", date(2007, 10, 9), None, None, None, "x", None)
    cursor = ReadCursor(rows=[row])

    result = get_overlapping_regimes(ReadConnection(cursor), date(2008, 1, 1), date(2008, 12, 31))

    assert result == [row]
    sql, params = cursor.executed[0]
    assert params == (date(2008, 12, 31), date(2008, 1, 1))
    assert "2008" not in sql


def test_count_regimes_returns_the_count():
    cursor = ReadCursor(one=(93,))

    assert count_regimes(ReadConnection(cursor)) == 93
    assert "COUNT(*)" in cursor.executed[0][0]
