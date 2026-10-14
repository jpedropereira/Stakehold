"""labs_view.build_default_mix (LB-6): the mix /labs backtests when the visitor hasn't edited
one -- the current holdings, weighted by market value as /portfolio weighs them."""

from datetime import date
from decimal import Decimal

from web.labs_view import MAX_BACKTEST_ASSETS, build_default_mix
from web.portfolio_client.base import Holding


def _holding(ticker, value):
    """A holding with only what the mix reads; unpriced when `value` is None."""
    priced = value is not None
    return Holding(
        canonical_ticker=ticker,
        quantity=Decimal("1"),
        average_cost_eur=Decimal("1"),
        cost_basis_eur=Decimal("1"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=Decimal("1"),
        market_value_native=Decimal(value) if priced else None,
        priced=priced,
        price_date=date(2026, 10, 9) if priced else None,
        market_value_eur=Decimal(value) if priced else None,
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


def test_weights_are_shares_of_the_priced_market_value():
    mix = build_default_mix([_holding("A", "300"), _holding("B", "100")])

    assert mix.assets == (("A", Decimal("0.75")), ("B", Decimal("0.25")))
    assert mix.unpriced == ()
    assert mix.unweighted == ()
    assert mix.dropped == ()


def test_largest_weight_comes_first_and_ties_go_by_ticker():
    mix = build_default_mix([_holding("C", "100"), _holding("B", "100"), _holding("A", "200")])

    assert [ticker for ticker, _ in mix.assets] == ["A", "B", "C"]


def test_unpriced_holdings_are_left_out_and_named():
    mix = build_default_mix([_holding("A", "100"), _holding("NEW", None)])

    assert mix.assets == (("A", Decimal("1")),)
    assert mix.unpriced == ("NEW",)


def test_a_holding_worth_nothing_is_left_out_rather_than_sent_with_weight_zero():
    """portfolio rejects a weight <= 0 for the whole mix (LB-4), so one such holding would
    cost every other asset its figures."""
    mix = build_default_mix([_holding("A", "100"), _holding("ZERO", "0")])

    assert mix.assets == (("A", Decimal("1")),)
    assert mix.unweighted == ("ZERO",)


def test_priced_holdings_summing_to_zero_leave_an_empty_mix():
    mix = build_default_mix([_holding("A", "0"), _holding("B", "0")])

    assert mix.assets == ()
    assert mix.unweighted == ("A", "B")


def test_no_holdings_is_an_empty_mix():
    mix = build_default_mix([])

    assert mix.assets == mix.unpriced == mix.unweighted == mix.dropped == ()


def test_more_holdings_than_the_backtest_takes_keeps_the_largest():
    holdings = [_holding(f"T{i:02d}", str(100 + i)) for i in range(MAX_BACKTEST_ASSETS + 2)]

    mix = build_default_mix(holdings)

    assert MAX_BACKTEST_ASSETS == 50
    assert len(mix.assets) == MAX_BACKTEST_ASSETS
    assert mix.dropped == ("T01", "T00")
    # The kept weights are still shares of the whole portfolio; portfolio re-normalizes them.
    assert mix.assets[0] == ("T51", Decimal("151") / sum(Decimal(100 + i) for i in range(52)))
