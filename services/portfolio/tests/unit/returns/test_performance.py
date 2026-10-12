"""returns/performance.py tested as a pure function -- ReturnSnapshot rows in, one summed point
per date out, each (account, ticker) forward-filled from its own last row.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.returns.calculator import ReturnSnapshot
from portfolio.returns.performance import PortfolioPoint, compute_series

_EIGHT_PLACES = Decimal("0.00000001")

D1 = date(2026, 1, 5)  # a Monday
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)


def _row(price_date, market_value_eur, *, ticker="IWDA.AS", dividends="0", account=1, cost="1000"):
    # market_value_eur, cost_basis_eur, dividends_received_eur and total_return_eur feed
    # compute_series' sums. The rest are filled with plausible values so the rows look like real
    # return_snapshots rows.
    mv = Decimal(market_value_eur)
    cost_basis = Decimal(cost)
    return ReturnSnapshot(
        account_id=account,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal("10.000000"),
        market_value_native=mv,
        market_value_eur=mv,
        cost_basis_eur=cost_basis,
        unrealized_return_eur=mv - cost_basis,
        unrealized_return_pct=None,
        dividends_received_eur=Decimal(dividends),
        total_return_eur=mv - cost_basis + Decimal(dividends),
        total_return_pct=None,
    )


def _q(value):
    return Decimal(value).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _values(points):
    return [(p.price_date, p.market_value_eur) for p in points]


def test_no_rows_means_no_points():
    assert compute_series([]) == []


def test_a_ticker_missing_a_day_holds_its_last_value():
    rows = [
        _row(D1, "1000"),
        _row(D2, "1100"),
        _row(D3, "1100"),
        _row(D1, "1000", ticker="VWCE.DE"),
        _row(D3, "1200", ticker="VWCE.DE"),
    ]

    # D2: VWCE holds its D1 1000.
    assert _values(compute_series(rows)) == [
        (D1, Decimal("2000")),
        (D2, Decimal("2100")),
        (D3, Decimal("2300")),
    ]


def test_a_ticker_contributes_nothing_before_its_first_row():
    rows = [_row(D1, "1000"), _row(D2, "1000"), _row(D2, "500", ticker="VWCE.DE")]

    assert _values(compute_series(rows)) == [(D1, Decimal("1000")), (D2, Decimal("1500"))]


def test_input_order_does_not_matter():
    rows = [_row(D2, "1100"), _row(D1, "1000")]

    assert _values(compute_series(rows)) == [(D1, Decimal("1000")), (D2, Decimal("1100"))]


# --- across accounts: rows are keyed on (account, ticker), never on ticker alone ---


def test_the_same_ticker_in_two_accounts_sums_each_accounts_own_rows():
    # Account 2 has no D2 row, so it must hold its own D1 value there, not account 1's.
    rows = [
        _row(D1, "1000", account=1),
        _row(D2, "1100", account=1),
        _row(D3, "1100", account=1),
        _row(D1, "500", account=2, cost="500"),
        _row(D3, "600", account=2, cost="500"),
    ]

    points = compute_series(rows)

    # D1: 1000 + 500. D2: 1100 + 500 (held). D3: 1100 + 600.
    assert points == [
        PortfolioPoint(
            price_date=D1,
            market_value_eur=Decimal("1500"),
            cost_basis_eur=Decimal("1500"),
            dividends_received_eur=Decimal("0"),
            total_return_eur=Decimal("0"),
            total_return_pct=_q("0"),
        ),
        PortfolioPoint(
            price_date=D2,
            market_value_eur=Decimal("1600"),
            cost_basis_eur=Decimal("1500"),
            dividends_received_eur=Decimal("0"),
            total_return_eur=Decimal("100"),
            total_return_pct=_q(Decimal(100) / Decimal(1500)),
        ),
        PortfolioPoint(
            price_date=D3,
            market_value_eur=Decimal("1700"),
            cost_basis_eur=Decimal("1500"),
            dividends_received_eur=Decimal("0"),
            total_return_eur=Decimal("200"),
            total_return_pct=_q(Decimal(200) / Decimal(1500)),
        ),
    ]


def test_total_return_pct_is_recomputed_from_the_sums_not_averaged():
    rows = [
        _row(D1, "1100", account=1),
        _row(D1, "3000", account=2, ticker="VWCE.DE", cost="3000", dividends="60"),
    ]

    [point] = compute_series(rows)

    # +10% and +2% average to 6%; the sums say 160 / 4000.
    assert point.dividends_received_eur == Decimal("60")
    assert point.total_return_eur == Decimal("160")
    assert point.total_return_pct == _q("0.04")


def test_total_return_pct_is_none_when_nothing_has_cost():
    [point] = compute_series([_row(D1, "0", cost="0")])

    assert point.cost_basis_eur == Decimal("0")
    assert point.total_return_pct is None
