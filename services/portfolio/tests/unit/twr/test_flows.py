"""twr/flows.py: resolves the user's accounts through accounts/repository, reads each one's
return and latest position rows by that account's own id, fetches every held ticker's series
from data-store, and hands the rest to twr/calculator.py. A ticker left out is named with its
reason, never dropped silently.

The calculator's arithmetic is tested in test_calculator.py; these tests check what is read,
fetched, excluded and logged. The repositories are faked; their SQL is covered by the
integration suite.
"""

import logging
import threading
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from portfolio.accounts.repository import Account
from portfolio.datastore_client.base import FxPoint, PricePoint, TickerInfo
from portfolio.errors import DataStoreApiError
from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.twr import flows
from portfolio.twr.flows import ExcludedHolding

TODAY = date(2026, 1, 9)
D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)

EUR = TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
USD = TickerInfo(exists=True, has_returns=True, currency="USD", fx_currency="USD")


def _account(account_id, user_id):
    return Account(
        id=account_id,
        user_id=user_id,
        broker="acme",
        account_label=f"acct-{account_id}",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _row(account_id, ticker, price_date, mv):
    mv = Decimal(mv)
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


def _position(account_id, ticker, quantity):
    return PositionSnapshot(
        account_id=account_id,
        canonical_ticker=ticker,
        as_of_date=D1,
        quantity=Decimal(quantity),
        average_cost_native=Decimal("100"),
        native_currency="EUR",
        average_cost_eur=Decimal("100"),
        cost_basis_eur=Decimal(quantity) * 100,
    )


def _prices(*returns):
    """D1 with data-store's NULL first return, then one return a day from D2."""
    return [PricePoint(D1, Decimal("100"), None)] + [
        PricePoint(date(2026, 1, 6 + i), Decimal("100"), Decimal(r)) for i, r in enumerate(returns)
    ]


class _FakeDataStoreClient:
    """Canned answers per ticker (or per currency, for FX). An Exception instance as the answer is
    raised instead. Calls are recorded; the fetch calls from worker threads, hence the lock."""

    def __init__(self, *, info=None, prices=None, fx=None):
        self._info = info or {}
        self._prices = prices or {}
        self._fx = fx or {}
        self._lock = threading.Lock()
        self.calls = []

    def _answer(self, call, answer):
        with self._lock:
            self.calls.append(call)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get_ticker_info(self, ticker):
        return self._answer(("info", ticker), self._info[ticker])

    def get_price_series(self, ticker, start, end):
        return self._answer(("prices", ticker, start, end), self._prices[ticker])

    def get_fx_rate_series(self, base, quote, start, end):
        return self._answer(("fx", base, quote, start, end), self._fx[quote])


def _fake_repositories(monkeypatch, user_id, *, rows, positions=None):
    """rows/positions: {account_id: [...]}. Records which account ids each read asked for."""
    reads = {"accounts": [], "returns": [], "positions": []}

    def list_by_user(conn, uid):
        reads["accounts"].append(uid)
        return [_account(account_id, user_id) for account_id in rows]

    def list_snapshots(conn, *, account_id):
        reads["returns"].append(account_id)
        return rows[account_id]

    def list_latest_positions(conn, account_id):
        reads["positions"].append(account_id)
        return (positions or {}).get(account_id, [])

    monkeypatch.setattr(flows.accounts_repository, "list_by_user", list_by_user)
    monkeypatch.setattr(flows.returns_repository, "list_snapshots", list_snapshots)
    monkeypatch.setattr(flows.positions_repository, "list_latest_snapshots", list_latest_positions)
    return reads


def test_reads_each_account_by_its_own_id_and_weights_across_them(monkeypatch):
    # Account 1 holds A (600), account 2 holds B (400). D2: A +10%, B -5%: r = 0.04.
    user_id = uuid.uuid4()
    reads = _fake_repositories(
        monkeypatch,
        user_id,
        rows={
            1: [_row(1, "A", D1, "600"), _row(1, "A", D2, "660")],
            2: [_row(2, "B", D1, "400"), _row(2, "B", D2, "380")],
        },
    )
    client = _FakeDataStoreClient(
        info={"A": EUR, "B": EUR}, prices={"A": _prices("0.10"), "B": _prices("-0.05")}
    )

    result = flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert reads == {"accounts": [user_id], "returns": [1, 2], "positions": [1, 2]}
    assert result.base_date == D1
    assert [(p.price_date, p.twr_return) for p in result.points] == [(D2, Decimal("0.04"))]
    assert result.excluded == []


def test_each_point_carries_its_day_and_each_included_ticker_its_currency(monkeypatch):
    # Ex-post risk (RK-11) masks VaR on the days and flags nonsync from the currencies. A (EUR)
    # and U (USD) are included; N isn't tracked, so it has no currency here.
    user_id = uuid.uuid4()
    _fake_repositories(
        monkeypatch,
        user_id,
        rows={1: [_row(1, t, d, "100") for t in ("A", "N", "U") for d in (D1, D2)]},
    )
    client = _FakeDataStoreClient(
        info={"A": EUR, "N": TickerInfo(exists=False, has_returns=False), "U": USD},
        prices={"A": _prices("0"), "U": _prices("0")},
        fx={"USD": [FxPoint(D1, Decimal("1.1")), FxPoint(D2, Decimal("1.1"))]},
    )

    result = flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert result.currencies == {"A": "EUR", "U": "USD"}
    assert [(d.price_date, d.tickers, d.traded) for d in result.days] == [
        (D2, frozenset({"A", "U"}), True)
    ]


def test_each_ticker_is_fetched_from_its_earliest_row_in_any_account(monkeypatch):
    user_id = uuid.uuid4()
    _fake_repositories(
        monkeypatch,
        user_id,
        rows={
            1: [_row(1, "A", D2, "100")],
            2: [_row(2, "A", D1, "100"), _row(2, "A", D2, "100")],
        },
    )
    client = _FakeDataStoreClient(info={"A": EUR}, prices={"A": _prices("0")})

    flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert [call for call in client.calls if call[0] == "prices"] == [("prices", "A", D1, TODAY)]


def test_left_out_tickers_are_named_with_their_reasons_and_the_rest_renormalised(monkeypatch):
    # A is included. N isn't tracked, F's price fetch fails, U is in USD with an empty FX series.
    # Only A is left, so r(D2) is A's own +2%.
    user_id = uuid.uuid4()
    _fake_repositories(
        monkeypatch,
        user_id,
        rows={
            1: [_row(1, t, d, "100") for t in ("A", "F", "N", "U") for d in (D1, D2)],
        },
    )
    client = _FakeDataStoreClient(
        info={"A": EUR, "F": EUR, "N": TickerInfo(exists=False, has_returns=False), "U": USD},
        prices={"A": _prices("0.02"), "F": DataStoreApiError("boom"), "U": _prices("0")},
        fx={"USD": []},
    )

    result = flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert [(p.price_date, p.twr_return) for p in result.points] == [(D2, Decimal("0.02"))]
    assert result.excluded == [
        ExcludedHolding("F", "failed", "couldn't reach data-store"),
        ExcludedHolding("N", "not_tracked", "not tracked"),
        ExcludedHolding("U", "no_fx_rate", "no FX series for USD"),
    ]


def test_a_ticker_whose_series_starts_after_its_fx_ends_has_no_fx_series(monkeypatch):
    # The FX series exists but ends before U's first price: eur_daily_returns finds no rate.
    user_id = uuid.uuid4()
    _fake_repositories(monkeypatch, user_id, rows={1: [_row(1, "U", D1, "100")]})
    client = _FakeDataStoreClient(
        info={"U": USD},
        prices={"U": _prices()},
        fx={"USD": [FxPoint(date(2027, 1, 1), Decimal("1.1"))]},
    )

    result = flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert result.excluded == [ExcludedHolding("U", "no_fx_rate", "no FX series for USD")]


def test_an_empty_price_series_has_no_history(monkeypatch):
    user_id = uuid.uuid4()
    _fake_repositories(monkeypatch, user_id, rows={1: [_row(1, "A", D1, "100")]})
    client = _FakeDataStoreClient(info={"A": EUR}, prices={"A": []})

    result = flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert result.excluded == [ExcludedHolding("A", "no_history", "no history yet")]


def test_a_closed_position_is_read_from_the_latest_position_rows(monkeypatch):
    # B's latest position is 0 and its last return row (D1) is stale at 500: after D1 it holds
    # no weight. D2: 0.5 x 0 + 0.5 x 0.02 = 0.01. D3: A only, flat: 0.
    d3 = date(2026, 1, 7)
    user_id = uuid.uuid4()
    _fake_repositories(
        monkeypatch,
        user_id,
        rows={
            1: [
                _row(1, "A", D1, "500"),
                _row(1, "A", D2, "500"),
                _row(1, "A", d3, "500"),
                _row(1, "B", D1, "500"),
            ]
        },
        positions={1: [_position(1, "A", "5"), _position(1, "B", "0")]},
    )
    client = _FakeDataStoreClient(
        info={"A": EUR, "B": EUR}, prices={"A": _prices("0", "0"), "B": _prices("0.02", "0.10")}
    )

    result = flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert [(p.price_date, p.twr_return) for p in result.points] == [
        (D2, Decimal("0.01")),
        (d3, Decimal("0")),
    ]


def test_a_user_with_no_return_rows_gets_an_empty_series_and_nothing_is_fetched(
    monkeypatch, caplog
):
    user_id = uuid.uuid4()
    _fake_repositories(monkeypatch, user_id, rows={1: []})
    client = _FakeDataStoreClient()

    with caplog.at_level(logging.INFO, logger="portfolio.twr.flows"):
        result = flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert result.points == [] and result.base_date is None and result.excluded == []
    assert client.calls == []
    assert "portfolio-twr: no return rows in any account yet, nothing to fetch." in caplog.text


def test_each_ticker_logs_its_outcome_and_a_failure_logs_its_exception(monkeypatch, caplog):
    user_id = uuid.uuid4()
    _fake_repositories(
        monkeypatch,
        user_id,
        rows={1: [_row(1, t, d, "100") for t in ("A", "F", "U") for d in (D1, D2)]},
    )
    client = _FakeDataStoreClient(
        info={"A": EUR, "F": EUR, "U": USD},
        prices={"A": _prices("0.02"), "F": DataStoreApiError("boom"), "U": _prices("0")},
        fx={"USD": []},
    )

    with caplog.at_level(logging.INFO, logger="portfolio.twr.flows"):
        flows.compute_portfolio_twr(object(), client, user_id=user_id, today=TODAY)

    assert "portfolio-twr: 3 ticker(s) held, rows from 2026-01-05 to 2026-01-06." in caplog.text
    assert "portfolio-twr: A: included, 1 day(s) of EUR returns, 0 not traded." in caplog.text
    assert "portfolio-twr: U: left out, no FX series for USD." in caplog.text
    failure = next(r for r in caplog.records if "F: failed" in r.getMessage())
    assert failure.levelno == logging.ERROR
    assert failure.exc_info is not None
