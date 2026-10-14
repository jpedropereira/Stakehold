"""labs_view.build_default_mix (LB-6): the mix /labs backtests when the visitor hasn't edited
one -- the current holdings, weighted by market value as /portfolio weighs them. LB-8:
build_growth_payload, the growth chart's data island."""

from datetime import date
from decimal import Decimal

from web.labs_view import MAX_BACKTEST_ASSETS, build_default_mix, build_growth_payload
from web.portfolio_client.base import BacktestGrowth, Holding


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


def _growth(mix, assets):
    return BacktestGrowth(
        period="Max",
        dates=(date(2021, 3, 15), date(2021, 3, 16)),
        mix=mix,
        assets=assets,
    )


def test_growth_payload_keeps_the_assets_order_and_their_missing_lines():
    growth = _growth(
        mix=(Decimal("0"), Decimal("0.5")),
        assets={"B": (Decimal("0"), Decimal("1")), "A": None},
    )

    assert build_growth_payload(growth) == {
        "period": "Max",
        "dates": ["2021-03-15", "2021-03-16"],
        "mix": [0.0, 0.5],
        "assets": [{"ticker": "B", "values": [0.0, 1.0]}, {"ticker": "A", "values": None}],
    }


def test_growth_payload_keeps_a_late_assets_null_points():
    growth = _growth(mix=(Decimal("0"), Decimal("0.5")), assets={"A": (None, Decimal("0"))})

    assert build_growth_payload(growth)["assets"] == [{"ticker": "A", "values": [None, 0.0]}]


def test_growth_payload_rounds_to_six_significant_digits():
    """Shorter than portfolio's eight-place strings, and still exact to the hundredth of a
    percent the tooltip shows."""
    growth = _growth(
        mix=(Decimal("0E-8"), Decimal("1.23456789012")),
        assets={"A": (Decimal("-0.000123456789"), Decimal("12.3456789"))},
    )

    payload = build_growth_payload(growth)

    assert payload["mix"] == [0.0, 1.23457]
    assert payload["assets"][0]["values"] == [-0.000123457, 12.3457]
    assert all(type(v) is float for v in payload["mix"] + payload["assets"][0]["values"])
