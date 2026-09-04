"""returns/calculator.py tested as a pure function -- no Postgres, no HTTP, just
PositionSnapshot/PricePoint/FxPoint/DividendEvent sequences in and ReturnSnapshot sequences out.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.datastore_client.base import FxPoint, PricePoint
from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import DividendEvent, compute_returns

_EIGHT_PLACES = Decimal("0.00000001")
_FOUR_PLACES = Decimal("0.0001")


def _position(**overrides):
    values = dict(
        account_id=1,
        canonical_ticker="IWDA.AS",
        as_of_date=date(2026, 1, 5),
        quantity=Decimal("10.000000"),
        average_cost_native=Decimal("95.000000"),
        native_currency="EUR",
        average_cost_eur=Decimal("95.000000"),
        cost_basis_eur=Decimal("950.0000"),
    )
    values.update(overrides)
    return PositionSnapshot(**values)


def _price(**overrides):
    values = dict(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))
    values.update(overrides)
    return PricePoint(**values)


def _fx(**overrides):
    values = dict(date=date(2026, 1, 6), rate=Decimal("1.1000"))
    values.update(overrides)
    return FxPoint(**values)


def _dividend(**overrides):
    values = dict(date=date(2026, 1, 10), eur_amount=Decimal("12.0000"))
    values.update(overrides)
    return DividendEvent(**values)


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def test_a_eur_position_needs_no_fx_lookup():
    snapshots = compute_returns(1, "IWDA.AS", [_position()], [_price()], {}, [])

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.price_date == date(2026, 1, 6)
    assert snap.quantity == Decimal("10.000000")
    assert snap.market_value_native == Decimal("1000.0000")
    assert snap.market_value_eur == Decimal("1000.0000")
    assert snap.cost_basis_eur == Decimal("950.0000")
    assert snap.unrealized_return_eur == Decimal("50.0000")
    assert snap.unrealized_return_pct == _pct(Decimal("50.0000"), Decimal("950.0000"))
    assert snap.dividends_received_eur == Decimal("0.0000")
    assert snap.total_return_eur == snap.unrealized_return_eur
    assert snap.total_return_pct == snap.unrealized_return_pct


def test_a_non_eur_position_converts_using_the_latest_fx_point_at_or_before_the_price_date():
    position = _position(native_currency="USD")
    price = _price(date=date(2026, 1, 6), split_adjusted_close=Decimal("110.00"))
    # Latest point at or before the price_date -- an earlier one, on purpose, to prove the
    # lookup isn't requiring an exact-date match.
    fx = _fx(date=date(2026, 1, 4), rate=Decimal("1.1000"))

    snapshots = compute_returns(1, "IWDA.AS", [position], [price], {"USD": [fx]}, [])

    snap = snapshots[0]
    assert snap.market_value_native == Decimal("1100.0000")
    assert snap.market_value_eur == (Decimal("1100.000000") / Decimal("1.1000")).quantize(
        _FOUR_PLACES, rounding=ROUND_HALF_UP
    )


def test_a_price_date_before_any_fx_point_is_skipped_not_zeroed():
    position = _position(native_currency="USD")
    early_price = _price(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))
    late_price = _price(date=date(2026, 1, 8), split_adjusted_close=Decimal("102.00"))
    # The first available FX point arrives after the first price date.
    fx = _fx(date=date(2026, 1, 7), rate=Decimal("1.1000"))

    snapshots = compute_returns(
        1, "IWDA.AS", [position], [early_price, late_price], {"USD": [fx]}, []
    )

    assert len(snapshots) == 1
    assert snapshots[0].price_date == date(2026, 1, 8)


def test_a_price_date_before_the_first_position_snapshot_is_skipped():
    position = _position(as_of_date=date(2026, 1, 5))
    too_early = _price(date=date(2026, 1, 3), split_adjusted_close=Decimal("90.00"))
    on_open = _price(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))

    snapshots = compute_returns(1, "IWDA.AS", [position], [too_early, on_open], {}, [])

    assert len(snapshots) == 1
    assert snapshots[0].price_date == date(2026, 1, 6)


def test_pct_is_none_not_zero_when_cost_basis_is_zero():
    closed_position = _position(
        quantity=Decimal("0.000000"),
        average_cost_eur=Decimal("0.000000"),
        cost_basis_eur=Decimal("0.0000"),
    )

    snapshots = compute_returns(1, "IWDA.AS", [closed_position], [_price()], {}, [])

    snap = snapshots[0]
    assert snap.market_value_eur == Decimal("0.0000")
    assert snap.unrealized_return_eur == Decimal("0.0000")
    assert snap.unrealized_return_pct is None
    assert snap.total_return_pct is None


def test_values_are_quantized_at_their_column_precision():
    position = _position(quantity=Decimal("3.333333"), cost_basis_eur=Decimal("333.3333"))
    price = _price(split_adjusted_close=Decimal("33.333333"))

    snapshots = compute_returns(1, "IWDA.AS", [position], [price], {}, [])

    snap = snapshots[0]
    assert snap.market_value_native.as_tuple().exponent == -4
    assert snap.market_value_eur.as_tuple().exponent == -4
    assert snap.unrealized_return_eur.as_tuple().exponent == -4
    assert snap.total_return_eur.as_tuple().exponent == -4
    assert snap.unrealized_return_pct.as_tuple().exponent == -8
    assert snap.total_return_pct.as_tuple().exponent == -8


def test_a_dividend_bumps_totals_only_from_the_date_it_is_received():
    position = _position()
    before = _price(date=date(2026, 1, 8), split_adjusted_close=Decimal("100.00"))
    after = _price(date=date(2026, 1, 12), split_adjusted_close=Decimal("100.00"))
    dividend = _dividend(date=date(2026, 1, 10), eur_amount=Decimal("12.0000"))

    snapshots = compute_returns(1, "IWDA.AS", [position], [before, after], {}, [dividend])

    before_snap, after_snap = snapshots
    assert before_snap.dividends_received_eur == Decimal("0.0000")
    assert before_snap.total_return_eur == before_snap.unrealized_return_eur
    assert after_snap.dividends_received_eur == Decimal("12.0000")
    assert after_snap.total_return_eur == after_snap.unrealized_return_eur + Decimal("12.0000")


def test_multiple_dividends_accumulate():
    price = _price(date=date(2026, 2, 1), split_adjusted_close=Decimal("100.00"))
    dividends = [
        _dividend(date=date(2026, 1, 10), eur_amount=Decimal("12.0000")),
        _dividend(date=date(2026, 1, 20), eur_amount=Decimal("8.5000")),
    ]

    snapshots = compute_returns(1, "IWDA.AS", [_position()], [price], {}, dividends)

    assert snapshots[0].dividends_received_eur == Decimal("20.5000")


def test_no_dividend_events_means_total_return_equals_unrealized_return():
    snapshots = compute_returns(1, "IWDA.AS", [_position()], [_price()], {}, [])

    assert snapshots[0].dividends_received_eur == Decimal("0.0000")
    assert snapshots[0].total_return_eur == snapshots[0].unrealized_return_eur
    assert snapshots[0].total_return_pct == snapshots[0].unrealized_return_pct


def test_a_dividend_during_a_reopened_position_still_accumulates():
    opened = _position(
        as_of_date=date(2026, 1, 5),
        quantity=Decimal("10.000000"),
        cost_basis_eur=Decimal("950.0000"),
    )
    closed = _position(
        as_of_date=date(2026, 1, 10),
        quantity=Decimal("0.000000"),
        average_cost_eur=Decimal("0.000000"),
        cost_basis_eur=Decimal("0.0000"),
    )
    reopened = _position(
        as_of_date=date(2026, 1, 20),
        quantity=Decimal("5.000000"),
        average_cost_eur=Decimal("120.000000"),
        cost_basis_eur=Decimal("600.0000"),
    )
    prices = [
        _price(date=date(2026, 1, 15), split_adjusted_close=Decimal("100.00")),  # while closed
        _price(date=date(2026, 1, 25), split_adjusted_close=Decimal("130.00")),  # after reopening
    ]
    # Paid after the reopening BUY.
    dividend = _dividend(date=date(2026, 1, 22), eur_amount=Decimal("5.0000"))

    snapshots = compute_returns(1, "IWDA.AS", [opened, closed, reopened], prices, {}, [dividend])

    while_closed, after_reopen = snapshots
    assert while_closed.dividends_received_eur == Decimal("0.0000")
    assert after_reopen.quantity == Decimal("5.000000")
    assert after_reopen.dividends_received_eur == Decimal("5.0000")
