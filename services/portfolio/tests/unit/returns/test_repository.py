from datetime import date
from decimal import Decimal

from portfolio.returns.calculator import ReturnSnapshot
from portfolio.returns.repository import upsert_snapshots


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
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, params_seq):
        self.executed.append((sql, list(params_seq)))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self):
        self._cursor = FakeCursor()
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def test_upsert_snapshots_is_a_true_upsert_not_delete_then_insert():
    conn = FakeConnection()

    upsert_snapshots(conn, [_snapshot(), _snapshot(price_date=date(2026, 1, 7))])

    executed = conn._cursor.executed
    assert len(executed) == 1  # one batched upsert, no separate delete
    sql, params_list = executed[0]
    assert "INSERT INTO return_snapshots" in sql
    assert "ON CONFLICT (account_id, canonical_ticker, price_date) DO UPDATE SET" in sql
    assert len(params_list) == 2


def test_upsert_snapshots_returns_the_count_upserted():
    conn = FakeConnection()

    saved = upsert_snapshots(conn, [_snapshot(), _snapshot(price_date=date(2026, 1, 7))])

    assert saved == 2


def test_upsert_snapshots_with_an_empty_sequence_does_nothing():
    conn = FakeConnection()

    saved = upsert_snapshots(conn, [])

    assert saved == 0
    assert conn._cursor.executed == []


def test_upsert_snapshots_does_not_commit():
    conn = FakeConnection()

    upsert_snapshots(conn, [_snapshot()])

    assert conn.committed is False


def test_upsert_snapshots_passes_the_snapshots_own_fields_including_dividends():
    conn = FakeConnection()
    snapshot = _snapshot()

    upsert_snapshots(conn, [snapshot])

    _, params_list = conn._cursor.executed[0]
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


def test_upsert_snapshots_handles_a_null_pct_for_a_zero_cost_basis_position():
    conn = FakeConnection()
    snapshot = _snapshot(
        cost_basis_eur=Decimal("0.0000"),
        unrealized_return_pct=None,
        total_return_pct=None,
    )

    upsert_snapshots(conn, [snapshot])

    _, params_list = conn._cursor.executed[0]
    assert params_list[0][8] is None  # unrealized_return_pct
    assert params_list[0][11] is None  # total_return_pct
