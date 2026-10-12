"""holdings/flows.list_holdings and list_portfolio_returns: both resolve the user's accounts
through accounts/repository (the isolation chokepoint) and read each one's rows by that account's
own id. list_holdings hands the latest rows to aggregate_holdings; list_portfolio_returns hands
every account's full return history and money flows to returns/performance.py. The repositories
are faked; their SQL is covered by the integration suite."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from portfolio.accounts.repository import Account
from portfolio.holdings import flows
from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.returns.performance import TickerGrowth, TradeFlow
from portfolio.ticker_mapping.base import TickerMappings

_PCT = Decimal("0.00000001")
D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)
_NO_MAPPINGS = TickerMappings(isin={}, symbol={})


def _account(account_id, label, user_id, broker="acme"):
    return Account(
        id=account_id,
        user_id=user_id,
        broker=broker,
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


# --- list_portfolio_returns ---


def _return_row(account_id, price_date, market_value_eur, *, ticker="IWDA.AS"):
    mv = Decimal(market_value_eur)
    return ReturnSnapshot(
        account_id=account_id,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal("10"),
        market_value_native=mv,
        market_value_eur=mv,
        cost_basis_eur=Decimal("1000"),
        unrealized_return_eur=mv - Decimal("1000"),
        unrealized_return_pct=None,
        dividends_received_eur=Decimal("0"),
        total_return_eur=mv - Decimal("1000"),
        total_return_pct=None,
    )


def _buy(account_id, on, amount, *, ticker="IWDA.AS"):
    return TradeFlow(
        account_id=account_id, canonical_ticker=ticker, date=on, eur_amount=-Decimal(amount)
    )


def _fake_portfolio(monkeypatch, *, accounts, rows, trade_flows):
    """Fakes the three reads list_portfolio_returns makes; returns the logs of what it asked."""
    reads = {"users": [], "rows": [], "flows": []}

    def list_by_user(c, uid):
        reads["users"].append(uid)
        return accounts

    def list_snapshots(c, *, account_id, canonical_ticker=None, start=None, end=None):
        # Growth is always computed from the first priced day: the read is never ranged.
        assert (canonical_ticker, start, end) == (None, None, None)
        reads["rows"].append(account_id)
        return [r for r in rows if r.account_id == account_id]

    def list_trade_flows(c, account, *, mappings):
        reads["flows"].append((account.id, account.broker, mappings))
        return [f for f in trade_flows if f.account_id == account.id]

    monkeypatch.setattr(flows.accounts_repository, "list_by_user", list_by_user)
    monkeypatch.setattr(flows.returns_repository, "list_snapshots", list_snapshots)
    monkeypatch.setattr(flows.pipeline, "list_trade_flows", list_trade_flows)
    return reads


def test_list_portfolio_returns_reads_each_account_by_its_own_id_and_broker(monkeypatch):
    user_id = uuid.uuid4()
    reads = _fake_portfolio(
        monkeypatch,
        accounts=[_account(1, "A", user_id), _account(2, "B", user_id, broker="globex")],
        rows=[
            _return_row(1, D1, "1000"),
            _return_row(1, D2, "1100"),
            _return_row(2, D1, "1000"),
            _return_row(2, D2, "1100"),
        ],
        trade_flows=[_buy(1, D1, "1000"), _buy(2, D1, "1000")],
    )

    result = flows.list_portfolio_returns(object(), user_id=user_id, mappings=_NO_MAPPINGS)

    assert reads == {
        "users": [user_id],
        "rows": [1, 2],
        "flows": [(1, "acme", _NO_MAPPINGS), (2, "globex", _NO_MAPPINGS)],
    }
    assert [(p.price_date, p.market_value_eur, p.growth_pct) for p in result.series] == [
        (D1, Decimal("2000"), Decimal("0").quantize(_PCT)),
        (D2, Decimal("2200"), Decimal("0.1").quantize(_PCT)),
    ]
    assert result.by_ticker == [
        TickerGrowth(canonical_ticker="IWDA.AS", growth_pct=Decimal("0.1").quantize(_PCT))
    ]


def test_start_and_end_crop_the_series_without_restarting_growth(monkeypatch):
    user_id = uuid.uuid4()
    _fake_portfolio(
        monkeypatch,
        accounts=[_account(1, "A", user_id)],
        rows=[_return_row(1, D1, "1000"), _return_row(1, D2, "1100"), _return_row(1, D3, "1210")],
        trade_flows=[_buy(1, D1, "1000")],
    )

    full = flows.list_portfolio_returns(object(), user_id=user_id, mappings=_NO_MAPPINGS)
    cropped = flows.list_portfolio_returns(
        object(), user_id=user_id, mappings=_NO_MAPPINGS, start=D2, end=D2
    )
    from_d2 = flows.list_portfolio_returns(
        object(), user_id=user_id, mappings=_NO_MAPPINGS, start=D2
    )
    to_d2 = flows.list_portfolio_returns(object(), user_id=user_id, mappings=_NO_MAPPINGS, end=D2)

    assert cropped.series == [full.series[1]]
    assert cropped.series[0].growth_pct == Decimal("0.1").quantize(_PCT)
    assert from_d2.series == full.series[1:]
    assert to_d2.series == full.series[:2]
    # by_ticker ignores the range: still growth to D3.
    assert cropped.by_ticker == full.by_ticker
    assert full.by_ticker[0].growth_pct == Decimal("0.21").quantize(_PCT)


def test_list_portfolio_returns_for_a_user_with_no_accounts_is_empty(monkeypatch):
    _fake_portfolio(monkeypatch, accounts=[], rows=[], trade_flows=[])

    result = flows.list_portfolio_returns(object(), user_id=uuid.uuid4(), mappings=_NO_MAPPINGS)

    assert result.series == []
    assert result.by_ticker == []


def test_an_account_with_no_return_rows_yet_contributes_nothing(monkeypatch):
    user_id = uuid.uuid4()
    _fake_portfolio(
        monkeypatch,
        accounts=[_account(1, "A", user_id), _account(2, "B", user_id)],
        rows=[_return_row(1, D1, "1000"), _return_row(1, D2, "1100")],
        # Account 2 bought, but its rebuild hasn't run: no rows to land the flow on.
        trade_flows=[_buy(1, D1, "1000"), _buy(2, D1, "5000")],
    )

    result = flows.list_portfolio_returns(object(), user_id=user_id, mappings=_NO_MAPPINGS)

    assert [(p.market_value_eur, p.growth_pct) for p in result.series] == [
        (Decimal("1000"), Decimal("0").quantize(_PCT)),
        (Decimal("1100"), Decimal("0.1").quantize(_PCT)),
    ]
