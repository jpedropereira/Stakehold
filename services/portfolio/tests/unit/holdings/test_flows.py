"""holdings/flows.list_holdings: resolves the user's accounts through accounts/repository (the
isolation chokepoint), reads each one's latest position and return rows by that account's own id,
and hands them to aggregate_holdings. The repositories are faked; their SQL is covered by the
integration suite."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from portfolio.accounts.repository import Account
from portfolio.holdings import flows
from portfolio.positions.calculator import PositionSnapshot


def _account(account_id, label, user_id):
    return Account(
        id=account_id,
        user_id=user_id,
        broker="acme",
        account_label=label,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _position(account_id, quantity):
    return PositionSnapshot(
        account_id=account_id,
        canonical_ticker="IWDA.AS",
        as_of_date=date(2026, 1, 2),
        quantity=Decimal(quantity),
        average_cost_native=Decimal("80"),
        native_currency="EUR",
        average_cost_eur=Decimal("80"),
        cost_basis_eur=Decimal(quantity) * 80,
    )


def test_list_holdings_reads_each_of_the_users_accounts_by_its_own_id(monkeypatch):
    user_id = uuid.uuid4()
    conn = object()
    asked_for_user = []
    position_reads = []
    return_reads = []

    def list_by_user(c, uid):
        assert c is conn
        asked_for_user.append(uid)
        return [_account(1, "A", user_id), _account(2, "B", user_id)]

    def list_latest_positions(c, account_id):
        position_reads.append(account_id)
        return [_position(account_id, "120" if account_id == 1 else "20")]

    def list_latest_returns(c, account_id):
        return_reads.append(account_id)
        return []

    monkeypatch.setattr(flows.accounts_repository, "list_by_user", list_by_user)
    monkeypatch.setattr(flows.positions_repository, "list_latest_snapshots", list_latest_positions)
    monkeypatch.setattr(flows.returns_repository, "list_latest_snapshots", list_latest_returns)

    result = flows.list_holdings(conn, user_id=user_id)

    assert asked_for_user == [user_id]
    assert position_reads == [1, 2]
    assert return_reads == [1, 2]
    assert [(h.canonical_ticker, h.quantity) for h in result.holdings] == [
        ("IWDA.AS", Decimal("140"))
    ]
    assert [e.account_label for e in result.holdings[0].accounts] == ["A", "B"]


def test_list_holdings_for_a_user_with_no_accounts_is_empty(monkeypatch):
    monkeypatch.setattr(flows.accounts_repository, "list_by_user", lambda c, uid: [])

    result = flows.list_holdings(object(), user_id=uuid.uuid4())

    assert result.holdings == []
    assert result.totals is None
