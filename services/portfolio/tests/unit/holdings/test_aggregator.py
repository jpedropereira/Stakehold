"""holdings/aggregator.aggregate_holdings: every account's latest position and return rows summed
into one row per canonical_ticker, plus the cross-account totals the home page shows.

The figures are hard-coded on purpose: each expected value is worked out by hand from the inputs
above it, so a test fails if the arithmetic changes, not just if the code does."""

from datetime import date
from decimal import Decimal

from portfolio.holdings.aggregator import (
    AccountHoldingInputs,
    HoldingAccount,
    HoldingsTotals,
    aggregate_holdings,
)
from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import ReturnSnapshot


def _position(account_id, ticker, quantity, *, currency="EUR", avg_native, avg_eur, cost):
    return PositionSnapshot(
        account_id=account_id,
        canonical_ticker=ticker,
        as_of_date=date(2026, 1, 2),
        quantity=Decimal(quantity),
        average_cost_native=Decimal(avg_native),
        native_currency=currency,
        average_cost_eur=Decimal(avg_eur),
        cost_basis_eur=Decimal(cost),
    )


def _return(
    account_id,
    ticker,
    quantity,
    *,
    price_date=date(2026, 1, 6),
    value_native,
    value_eur,
    cost,
    dividends="0.0000",
):
    unrealized = Decimal(value_eur) - Decimal(cost)
    total = unrealized + Decimal(dividends)
    cost_d = Decimal(cost)
    return ReturnSnapshot(
        account_id=account_id,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal(quantity),
        market_value_native=Decimal(value_native),
        market_value_eur=Decimal(value_eur),
        cost_basis_eur=cost_d,
        unrealized_return_eur=unrealized,
        unrealized_return_pct=None if cost_d == 0 else unrealized / cost_d,
        dividends_received_eur=Decimal(dividends),
        total_return_eur=total,
        total_return_pct=None if cost_d == 0 else total / cost_d,
    )


def _account(label, positions=(), returns=()):
    return AccountHoldingInputs(account_label=label, positions=positions, returns=returns)


def _iwda_two_accounts():
    a = _account(
        "A",
        positions=[_position(1, "IWDA.AS", "120", avg_native="80", avg_eur="80", cost="9600")],
        returns=[
            _return(
                1,
                "IWDA.AS",
                "120",
                value_native="12000",
                value_eur="12000",
                cost="9600",
                dividends="100",
            )
        ],
    )
    b = _account(
        "B",
        positions=[_position(2, "IWDA.AS", "20", avg_native="95", avg_eur="95", cost="1900")],
        returns=[
            _return(
                2,
                "IWDA.AS",
                "20",
                price_date=date(2026, 1, 5),
                value_native="2000",
                value_eur="2000",
                cost="1900",
            )
        ],
    )
    return a, b


def test_the_same_ticker_in_two_accounts_sums_into_one_row():
    result = aggregate_holdings(list(_iwda_two_accounts()))

    assert len(result.holdings) == 1
    row = result.holdings[0]
    assert row.canonical_ticker == "IWDA.AS"
    assert row.quantity == Decimal("140")
    assert row.cost_basis_eur == Decimal("11500")
    assert row.average_cost_eur == Decimal("82.142857")  # 11500 / 140
    assert row.market_value_eur == Decimal("14000")
    assert row.unrealized_return_eur == Decimal("2500")
    assert row.dividends_received_eur == Decimal("100")
    assert row.total_return_eur == Decimal("2600")
    assert row.priced is True


def test_percentages_are_recomputed_from_the_sums_not_averaged():
    row = aggregate_holdings(list(_iwda_two_accounts())).holdings[0]

    # 2500 / 11500 and 2600 / 11500. Averaging A's 25% and B's 5.26% would give neither.
    assert row.unrealized_return_pct == Decimal("0.21739130")
    assert row.total_return_pct == Decimal("0.22608696")


def test_the_breakdown_lists_every_account_in_label_order():
    a, b = _iwda_two_accounts()

    row = aggregate_holdings([b, a]).holdings[0]

    assert row.accounts == (
        HoldingAccount(
            account_label="A",
            quantity=Decimal("120"),
            native_currency="EUR",
            average_cost_native=Decimal("80"),
            cost_basis_eur=Decimal("9600"),
            market_value_native=Decimal("12000"),
            market_value_eur=Decimal("12000"),
            unrealized_return_eur=Decimal("2400"),
        ),
        HoldingAccount(
            account_label="B",
            quantity=Decimal("20"),
            native_currency="EUR",
            average_cost_native=Decimal("95"),
            cost_basis_eur=Decimal("1900"),
            market_value_native=Decimal("2000"),
            market_value_eur=Decimal("2000"),
            unrealized_return_eur=Decimal("100"),
        ),
    )


def test_price_date_is_the_newest_of_the_rows_return_dates():
    row = aggregate_holdings(list(_iwda_two_accounts())).holdings[0]

    assert row.price_date == date(2026, 1, 6)


def test_one_currency_weights_the_native_average_cost_by_quantity():
    row = aggregate_holdings(list(_iwda_two_accounts())).holdings[0]

    assert row.native_currency == "EUR"
    assert row.mixed_currency is False
    assert row.average_cost_native == Decimal("82.142857")  # (120*80 + 20*95) / 140
    assert row.market_value_native == Decimal("14000")


def test_mixed_currencies_null_the_native_figures_but_still_sum_the_euros():
    a = _account(
        "A",
        positions=[_position(1, "VUSA", "10", avg_native="50", avg_eur="50", cost="500")],
        returns=[_return(1, "VUSA", "10", value_native="600", value_eur="600", cost="500")],
    )
    b = _account(
        "B",
        positions=[
            _position(2, "VUSA", "10", currency="GBP", avg_native="40", avg_eur="48", cost="480")
        ],
        returns=[_return(2, "VUSA", "10", value_native="500", value_eur="590", cost="480")],
    )

    row = aggregate_holdings([a, b]).holdings[0]

    assert row.native_currency is None
    assert row.mixed_currency is True
    assert row.average_cost_native is None
    assert row.market_value_native is None
    assert row.market_value_eur == Decimal("1190")
    assert row.cost_basis_eur == Decimal("980")
    assert [
        (e.native_currency, e.average_cost_native, e.market_value_native) for e in row.accounts
    ] == [
        ("EUR", Decimal("50"), Decimal("600")),
        ("GBP", Decimal("40"), Decimal("500")),
    ]


def test_different_tickers_stay_separate_rows_sorted_by_ticker():
    a = _account(
        "A",
        positions=[
            _position(1, "VUSA", "5", avg_native="50", avg_eur="50", cost="250"),
            _position(1, "IWDA.AS", "1", avg_native="80", avg_eur="80", cost="80"),
        ],
    )

    result = aggregate_holdings([a])

    assert [h.canonical_ticker for h in result.holdings] == ["IWDA.AS", "VUSA"]


def test_an_unpriced_holding_counts_units_and_cost_but_no_value():
    a = _account("A", positions=[_position(1, "NEW", "10", avg_native="5", avg_eur="5", cost="50")])

    row = aggregate_holdings([a]).holdings[0]

    assert row.priced is False
    assert row.quantity == Decimal("10")
    assert row.cost_basis_eur == Decimal("50")
    assert row.average_cost_eur == Decimal("5.000000")
    assert row.average_cost_native == Decimal("5.000000")
    assert row.price_date is None
    for field in (
        "market_value_native",
        "market_value_eur",
        "unrealized_return_eur",
        "unrealized_return_pct",
        "dividends_received_eur",
        "total_return_eur",
        "total_return_pct",
    ):
        assert getattr(row, field) is None, field
    assert row.accounts[0].market_value_eur is None


def test_a_partly_priced_holding_shows_no_partial_sum():
    a, _ = _iwda_two_accounts()
    b = _account(
        "B", positions=[_position(2, "IWDA.AS", "20", avg_native="95", avg_eur="95", cost="1900")]
    )

    row = aggregate_holdings([a, b]).holdings[0]

    assert row.priced is False
    assert row.quantity == Decimal("140")
    assert row.market_value_eur is None
    assert row.market_value_native is None
    assert row.total_return_eur is None
    assert row.price_date == date(2026, 1, 6)
    assert row.accounts[0].market_value_eur == Decimal("12000")
    assert row.accounts[1].market_value_eur is None


def test_a_closed_position_is_not_a_row_but_its_dividends_count_in_the_totals():
    a = _account(
        "A",
        positions=[_position(1, "OLD", "0", avg_native="0", avg_eur="0", cost="0")],
        returns=[_return(1, "OLD", "0", value_native="0", value_eur="0", cost="0", dividends="30")],
    )

    result = aggregate_holdings([a])

    assert result.holdings == []
    assert result.totals.dividends_received_eur == Decimal("30")
    assert result.totals.total_return_eur == Decimal("30")
    assert result.totals.total_return_pct is None  # the cost is 0


def test_a_ticker_closed_in_one_account_and_open_in_another_shows_only_the_open_one():
    a = _account(
        "A",
        positions=[_position(1, "IWDA.AS", "0", avg_native="0", avg_eur="0", cost="0")],
        returns=[
            _return(1, "IWDA.AS", "0", value_native="0", value_eur="0", cost="0", dividends="30")
        ],
    )
    _, b = _iwda_two_accounts()

    result = aggregate_holdings([a, b])

    row = result.holdings[0]
    assert [e.account_label for e in row.accounts] == ["B"]
    assert row.quantity == Decimal("20")
    assert row.dividends_received_eur == Decimal("0")
    assert result.totals.dividends_received_eur == Decimal("30")


def test_zero_cost_leaves_both_percentages_null():
    a = _account(
        "A",
        positions=[_position(1, "FREE", "3", avg_native="0", avg_eur="0", cost="0")],
        returns=[_return(1, "FREE", "3", value_native="30", value_eur="30", cost="0")],
    )

    row = aggregate_holdings([a]).holdings[0]

    assert row.unrealized_return_eur == Decimal("30")
    assert row.unrealized_return_pct is None
    assert row.total_return_pct is None


def test_a_negative_quantity_is_kept_and_a_net_zero_row_has_no_average_cost():
    a = _account("A", positions=[_position(1, "ODD", "10", avg_native="5", avg_eur="5", cost="50")])
    b = _account(
        "B", positions=[_position(2, "ODD", "-10", avg_native="5", avg_eur="5", cost="-50")]
    )

    row = aggregate_holdings([a, b]).holdings[0]

    assert row.quantity == Decimal("0")
    assert row.average_cost_eur is None
    assert row.average_cost_native is None
    assert [e.quantity for e in row.accounts] == [Decimal("10"), Decimal("-10")]


def test_the_totals_follow_the_home_pages_rule():
    a, b = _iwda_two_accounts()
    a = _account(
        "A",
        positions=list(a.positions)
        + [_position(1, "OLD", "0", avg_native="0", avg_eur="0", cost="0")],
        returns=list(a.returns)
        + [
            _return(
                1,
                "OLD",
                "0",
                price_date=date(2026, 1, 7),
                value_native="0",
                value_eur="0",
                cost="0",
                dividends="25",
            )
        ],
    )
    unpriced = _account(
        "C", positions=[_position(3, "NEW", "1", avg_native="9", avg_eur="9", cost="9")]
    )

    result = aggregate_holdings([a, b, unpriced])

    # A: 12000 value, 9600 cost, 125 dividends, 2525 total. B: 2000, 1900, 0, 100. C: nothing.
    assert result.totals == HoldingsTotals(
        price_date=date(2026, 1, 7),
        market_value_eur=Decimal("14000"),
        cost_basis_eur=Decimal("11500"),
        dividends_received_eur=Decimal("125"),
        total_return_eur=Decimal("2625"),
        total_return_pct=Decimal("0.22826087"),
    )


def test_no_return_rows_anywhere_leaves_the_totals_null():
    a = _account("A", positions=[_position(1, "NEW", "1", avg_native="9", avg_eur="9", cost="9")])

    assert aggregate_holdings([a]).totals is None


def test_an_empty_portfolio_has_no_holdings_and_no_totals():
    result = aggregate_holdings([])

    assert result.holdings == []
    assert result.totals is None
