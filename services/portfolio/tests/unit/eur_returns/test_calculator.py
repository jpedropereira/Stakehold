"""eur_returns/calculator.py tested as a pure function: an asset's daily simple_return (and FX
series, when it isn't in EUR) in, its daily EUR return series out, with its local and FX
components beside it (risk spec §4.2).

Every expected value is worked out by hand in the comment beside it. Returns are fractions
(0.02 is +2%). fx is units of the currency per 1 EUR, as fx_rates stores it.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from portfolio.datastore_client.base import FxPoint, PricePoint
from portfolio.eur_returns.calculator import (
    NO_FX_SERIES,
    NO_HISTORY,
    EurReturns,
    Ineligible,
    eur_daily_returns,
    traded_dates,
)
from portfolio.labs.calculator import AssetInput, Period, compute_backtest

_EIGHT_PLACES = Decimal("0.00000001")
_ONE = Decimal("1")

D0 = date(2026, 1, 5)  # a Monday
D1 = date(2026, 1, 6)
D2 = date(2026, 1, 7)
D3 = date(2026, 1, 8)
D4 = date(2026, 1, 9)


def _prices(points):
    """points: (date, simple_return as a string, or None for a gap). The close plays no part in
    the calculation, so it's a constant."""
    return [PricePoint(d, Decimal("100"), None if r is None else Decimal(r)) for d, r in points]


def _fx(points):
    return [FxPoint(d, Decimal(rate)) for d, rate in points]


def _q(value):
    return Decimal(value).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def test_the_spec_worked_example():
    # §4.2: a USD stock +2.0% local while EURUSD moves 1.10 -> 1.12 (the dollar weakened).
    # r_fx = 1.10 / 1.12 - 1 = -1.7857%; r_eur = 1.02 x 1.10 / 1.12 - 1 = +0.1786%.
    result = eur_daily_returns(
        _prices([(D0, None), (D1, "0.02")]), _fx([(D0, "1.10"), (D1, "1.12")])
    )

    assert isinstance(result, EurReturns)
    assert result.anchor == D0
    assert result.dates == [D1]
    assert result.r_local == [Decimal("0.02")]
    assert round(result.r_fx[0], 6) == Decimal("-0.017857")
    assert round(result.r_eur[0], 6) == Decimal("0.001786")
    # r_eur is the two components compounded, not added.
    assert _ONE + result.r_eur[0] == (_ONE + result.r_local[0]) * (_ONE + result.r_fx[0])


def test_a_eur_asset_has_no_fx_component():
    result = eur_daily_returns(_prices([(D0, None), (D1, "0.02"), (D2, "-0.01")]), None)

    assert result.anchor == D0
    assert result.dates == [D1, D2]
    assert result.r_fx == [Decimal("0"), Decimal("0")]
    assert result.r_eur == result.r_local == [Decimal("0.02"), Decimal("-0.01")]
    assert result.fx is None


def test_fx_is_taken_on_or_before_each_date():
    # No rate on D2, so D1's 1.25 applies to it. D0 -> D1: 1.00 / 1.25 - 1 = -0.20;
    # D1 -> D2: flat; D2 -> D3: 1.25 / 1.00 - 1 = +0.25.
    result = eur_daily_returns(
        _prices([(D0, None), (D1, "0"), (D2, "0"), (D3, "0")]),
        _fx([(D0, "1.00"), (D1, "1.25"), (D3, "1.00")]),
    )

    assert result.r_fx == [Decimal("-0.2"), Decimal("0"), Decimal("0.25")]
    assert result.fx.on(D2) == Decimal("1.25")


def test_a_rate_on_a_day_the_asset_did_not_trade_counts_on_its_next_day():
    # The asset doesn't trade on D1, but the rate moves then: D0 -> D2 is 1.00 / 0.80 - 1.
    result = eur_daily_returns(_prices([(D0, None), (D2, "0")]), _fx([(D0, "1.00"), (D1, "0.80")]))

    assert result.dates == [D2]
    assert result.r_fx == [Decimal("0.25")]


def test_the_series_starts_on_the_first_price_with_a_rate_on_or_before_it():
    # Rates start on D2. D1's and D2's returns have no rate on the day before them, so D2 is the
    # anchor and D3's return is the first.
    result = eur_daily_returns(
        _prices([(D0, None), (D1, "0.50"), (D2, "0.10"), (D3, "0.20")]),
        _fx([(D2, "1.00"), (D3, "1.00")]),
    )

    assert result.anchor == D2
    assert result.dates == [D3]
    assert result.r_eur == [Decimal("0.20")]


def test_a_rate_dated_between_two_prices_starts_the_series_at_the_later_one():
    # The first rate is on D2, a day the asset didn't trade; the first price on or after it is D3.
    result = eur_daily_returns(
        _prices([(D0, None), (D1, "0.50"), (D3, "0.10"), (D4, "0.20")]),
        _fx([(D2, "1.00")]),
    )

    assert result.anchor == D3
    assert result.dates == [D4]


def test_rates_starting_before_the_prices_start_the_series_on_the_first_price():
    result = eur_daily_returns(
        _prices([(D1, None), (D2, "0.10")]), _fx([(D0, "1.00"), (D2, "1.00")])
    )

    assert result.anchor == D1
    assert result.dates == [D2]


def test_the_first_prices_own_return_is_not_part_of_the_series():
    # data-store's first day is normally NULL, but if it isn't, it has no prior close in the
    # series to be a return over.
    result = eur_daily_returns(_prices([(D0, "0.30"), (D1, "0.10")]), None)

    assert result.anchor == D0
    assert result.r_local == [Decimal("0.10")]


def test_a_null_return_stays_a_gap_and_its_fx_move_is_still_reported():
    # R5: a NULL is a gap, never 0. The rate still moved: 1.00 / 1.25 - 1.
    result = eur_daily_returns(
        _prices([(D0, None), (D1, None), (D2, "0.10")]),
        _fx([(D0, "1.00"), (D1, "1.25"), (D2, "1.25")]),
    )

    assert result.dates == [D1, D2]
    assert result.r_local == [None, Decimal("0.10")]
    assert result.r_eur == [None, Decimal("0.10")]
    assert result.r_fx == [Decimal("-0.2"), Decimal("0")]


def test_a_single_usable_price_has_no_returns_yet():
    result = eur_daily_returns(_prices([(D0, None)]), None)

    assert result == EurReturns(
        anchor=D0, dates=[], r_local=[], r_fx=[], r_eur=[], fx=None, traded=frozenset()
    )


def test_no_prices_is_ineligible_for_having_no_history():
    assert eur_daily_returns([], None) == Ineligible(NO_HISTORY)
    assert eur_daily_returns([], _fx([(D0, "1.00")])) == Ineligible(NO_HISTORY)


@pytest.mark.parametrize(
    "fx",
    [
        pytest.param([], id="no rates at all"),
        pytest.param(_fx([(D3, "1.00")]), id="rates start after the last price"),
    ],
)
def test_no_usable_fx_series_is_ineligible(fx):
    result = eur_daily_returns(_prices([(D0, None), (D1, "0.10"), (D2, "0")]), fx)

    assert result == Ineligible(NO_FX_SERIES)


def _chained(result):
    """1 + cumulative EUR return at the anchor and at each date, as Labs would emit it."""
    growth = [_q("0")]
    level = _ONE
    for r in result.r_eur:
        level *= _ONE + r
        growth.append(_q(level - _ONE))
    return growth


@pytest.mark.parametrize(
    "fx",
    [
        pytest.param(
            [(D0, "1.0832"), (D1, "1.0791"), (D2, "1.0913"), (D3, "1.0877"), (D4, "1.0790")],
            id="USD",
        ),
        pytest.param(None, id="EUR"),
    ],
)
def test_chained_eur_returns_equal_labs_growth(fx):
    # §4.2's CHECK: one conversion, so the risk path's chained returns and Labs' growth line
    # give the same numbers on a series without gaps.
    points = [(D0, None), (D1, "0.0123"), (D2, "-0.0371"), (D3, "0.0042"), (D4, "0.0188")]
    fx_points = None if fx is None else _fx(fx)

    result = eur_daily_returns(_prices(points), fx_points)
    labs = compute_backtest(
        [AssetInput("A", Decimal("1"), _prices(points), fx_points)], growth_period=Period.MAX
    )

    assert labs.growth.dates == [result.anchor, *result.dates]
    assert labs.growth.assets["A"] == _chained(result)


# --- traded days: the VaR day mask's input (RK-31, spec R7) ---
#
# A day is untraded when its volume is 0 and its price didn't move (a flat or NULL return): a
# closed exchange's stale holiday row. A thin listing's zero-volume day that moved is a quoted
# price, so it counts. A NULL volume is unknown, not zero, and counts as traded.


def _point(day, simple_return, volume):
    return PricePoint(
        day, Decimal("100"), None if simple_return is None else Decimal(simple_return), volume
    )


@pytest.mark.parametrize(
    ("simple_return", "volume", "traded"),
    [
        pytest.param("0.01", 1200, True, id="volume-and-a-move"),
        pytest.param("0", 1200, True, id="volume-and-a-flat-day"),
        pytest.param("0", 0, False, id="zero-volume-flat-holiday-row"),
        pytest.param(None, 0, False, id="zero-volume-null-return"),
        pytest.param("-0.009", 0, True, id="zero-volume-quoted-move"),
        pytest.param("0", None, True, id="null-volume-counts-as-traded"),
        pytest.param(None, None, True, id="null-volume-null-return"),
    ],
)
def test_traded_dates_applies_the_mask_rule(simple_return, volume, traded):
    assert traded_dates([_point(D1, simple_return, volume)]) == (
        frozenset({D1}) if traded else frozenset()
    )


def test_eur_returns_carry_the_traded_days_among_their_own_dates():
    # D2 is a UK-bank-holiday-style row: zero volume and flat. It's still a return date (the next
    # day's return runs from it), but it isn't a traded day. The anchor isn't a return date.
    prices = [
        _point(D0, None, 1000),
        _point(D1, "0.01", 1000),
        _point(D2, "0", 0),
        _point(D3, "0.02", 0),  # a thin listing's quoted move
    ]

    result = eur_daily_returns(prices, None)

    assert result.dates == [D1, D2, D3]
    assert result.traded == frozenset({D1, D3})
