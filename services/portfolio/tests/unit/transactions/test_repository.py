from datetime import UTC, datetime
from decimal import Decimal

from portfolio.transactions.base import CanonicalTransaction
from portfolio.transactions.repository import (
    insert_transactions,
    list_by_account,
    list_distinct_instruments,
)

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


def _txn(**overrides):
    values = dict(
        account_id=1,
        broker_transaction_ref="manual-1",
        transaction_type="BUY",
        trade_datetime=NOW,
        instrument_identifier="IE00B4L5Y983",
        instrument_identifier_kind="ISIN",
        quantity=Decimal("10"),
        price=Decimal("95.50"),
        native_currency="EUR",
        native_amount=Decimal("-955.00"),
        eur_amount=Decimal("-955.0000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )
    values.update(overrides)
    return CanonicalTransaction(**values)


class FakeCursor:
    def __init__(self, rowcounts):
        # One rowcount per expected execute() call, consumed in order -- lets a test simulate
        # ON CONFLICT DO NOTHING skipping a specific row.
        self._rowcounts = iter(rowcounts)
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.rowcount = next(self._rowcounts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, rowcounts):
        self._cursor = FakeCursor(rowcounts)
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def test_insert_transactions_executes_a_parameterized_insert_per_row():
    conn = FakeConnection(rowcounts=[1])

    saved = insert_transactions(conn, [_txn()])

    sql, params = conn._cursor.executed[0]
    assert "INSERT INTO transactions" in sql
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert params[0] == 1  # account_id
    assert params[1] == "manual-1"  # broker_transaction_ref
    assert saved == 1
    # The caller (pipeline.py) owns the transaction.
    assert conn.committed is False


def test_insert_transactions_returns_the_count_actually_written():
    conn = FakeConnection(rowcounts=[1, 0, 1])

    saved = insert_transactions(conn, [_txn(broker_transaction_ref=r) for r in ("a", "b", "c")])

    assert saved == 2


def test_insert_transactions_with_an_empty_sequence_executes_nothing():
    conn = FakeConnection(rowcounts=[])

    saved = insert_transactions(conn, [])

    assert saved == 0
    assert conn._cursor.executed == []


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


def test_list_distinct_instruments_returns_kind_identifier_broker_triples():
    conn = FakeReadConnection(rows=[("ISIN", "IE00B4L5Y983", "acme"), ("SYMBOL", "IWDA", "globex")])

    result = list_distinct_instruments(conn, user_id="u1")

    assert result == [("ISIN", "IE00B4L5Y983", "acme"), ("SYMBOL", "IWDA", "globex")]


def test_list_distinct_instruments_scopes_by_user_id():
    conn = FakeReadConnection(rows=[])

    list_distinct_instruments(conn, user_id="u1")

    sql, params = conn._cursor.executed[0]
    assert "accounts" in sql
    assert params == ("u1",)


def test_list_distinct_instruments_filters_out_cash_only_rows():
    conn = FakeReadConnection(rows=[])

    list_distinct_instruments(conn, user_id="u1")

    sql, _ = conn._cursor.executed[0]
    assert "IS NOT NULL" in sql


# --- list_by_account ---


def test_list_by_account_returns_canonical_transactions_in_row_order():
    txn = _txn()
    conn = FakeReadConnection(
        rows=[
            (
                txn.account_id,
                txn.broker_transaction_ref,
                txn.transaction_type,
                txn.trade_datetime,
                txn.instrument_identifier,
                txn.instrument_identifier_kind,
                txn.quantity,
                txn.price,
                txn.native_currency,
                txn.native_amount,
                txn.eur_amount,
                txn.fx_rate,
                txn.fx_rate_source,
                txn.fees_native,
            )
        ]
    )

    result = list_by_account(conn, account_id=1)

    assert result == [txn]


def test_list_by_account_scopes_by_account_id():
    conn = FakeReadConnection(rows=[])

    list_by_account(conn, account_id=42)

    sql, params = conn._cursor.executed[0]
    assert "WHERE account_id = %s" in sql
    assert params == (42,)


def test_list_by_account_orders_by_trade_datetime_then_ref():
    conn = FakeReadConnection(rows=[])

    list_by_account(conn, account_id=1)

    sql, _ = conn._cursor.executed[0]
    assert "ORDER BY trade_datetime, broker_transaction_ref" in sql
