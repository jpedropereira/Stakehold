"""labs/calculator.py tested as a pure function: each asset's daily simple_return (and FX series,
when it isn't in EUR) in, every period's % return for each asset and the buy-and-hold mix out.

Every expected value is worked out by hand in the comment beside it. Returns are fractions
(0.10 is +10%), quantized to 8 places like portfolio's other pct figures.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from portfolio.datastore_client.base import FxPoint, PricePoint
from portfolio.labs.calculator import AssetInput, Period, compute_backtest

_EIGHT_PLACES = Decimal("0.00000001")

D0 = date(2026, 1, 5)  # a Monday
D1 = date(2026, 1, 6)
D2 = date(2026, 1, 7)
D3 = date(2026, 1, 8)


def _q(value):
    return Decimal(value).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _asset(ticker, weight, points, fx=None):
    """points: (date, simple_return as a string, or None for a gap). The close plays no part in
    the calculation, so it's a constant."""
    prices = [PricePoint(d, Decimal("100"), None if r is None else Decimal(r)) for d, r in points]
    if fx is not None:
        fx = [FxPoint(d, Decimal(rate)) for d, rate in fx]
    return AssetInput(ticker=ticker, weight=Decimal(weight), prices=prices, fx=fx)


def _row(result, ticker):
    return next(row for row in result.assets if row.ticker == ticker)


def test_one_asset_chains_its_returns_over_each_period():
    asset = _asset(
        "A",
        "1",
        [(date(2026, 1, 30), None), (date(2026, 2, 27), "0.10"), (date(2026, 3, 31), "-0.10")],
    )

    result = compute_backtest([asset], growth_period=Period.MAX)

    assert result.end_date == date(2026, 3, 31)
    row = _row(result, "A")
    # 1M: 03-31 minus a month is 02-31, clamped to 02-28; the base is the close on or before it
    # (02-27), so only the -10% day is inside.
    assert row.periods[Period.M1].start == date(2026, 2, 28)
    assert row.periods[Period.M1].return_pct == _q("-0.10")
    # 3M starts 2025-12-31, before the asset's first day.
    assert row.periods[Period.M3].covered is False
    assert row.periods[Period.M3].return_pct is None
    # Max: 1.10 x 0.90 - 1.
    assert row.periods[Period.MAX].start == date(2026, 1, 30)
    assert row.periods[Period.MAX].return_pct == _q("-0.01")
    # One asset: the mix is that asset.
    assert result.mix.periods[Period.MAX].return_pct == _q("-0.01")
    assert result.mix.periods[Period.M3].return_pct is None


def test_every_period_is_reported_for_every_row():
    asset = _asset("A", "1", [(D0, None), (D1, "0.01")])

    result = compute_backtest([asset], growth_period=Period.MAX)

    assert list(result.mix.periods) == list(Period)
    assert list(_row(result, "A").periods) == list(Period)


def test_the_mix_is_bought_once_and_its_weights_drift():
    # A doubles then halves; B is flat. Bought 50/50 and held, the mix ends where it started:
    # 0.5 x (2 x 0.5) + 0.5 x 1 - 1 = 0. Rebalanced to 50/50 daily it would read
    # 1.5 x (0.5 x 0.5 + 0.5 x 1) - 1 = +12.5%, which buy and hold must not.
    a = _asset("A", "0.5", [(D0, None), (D1, "1.0"), (D2, "-0.5")])
    b = _asset("B", "0.5", [(D0, None), (D1, "0"), (D2, "0")])

    result = compute_backtest([a, b], growth_period=Period.MAX)

    assert result.mix.periods[Period.MAX].return_pct == _q("0")
    # On D1 the mix is 0.5 x 2 + 0.5 x 1 - 1.
    assert result.growth.mix == [_q("0"), _q("0.5"), _q("0")]


def test_assets_on_different_calendars_line_up_and_carry_forward():
    # B doesn't trade on D1 (a holiday on its exchange), so its growth carries D0's value.
    a = _asset("A", "0.5", [(D0, None), (D1, "0.10"), (D2, "0"), (D3, "0")])
    b = _asset("B", "0.5", [(D0, None), (D2, "0.20"), (D3, "0")])

    result = compute_backtest([a, b], growth_period=Period.MAX)

    growth = result.growth
    assert growth.dates == [D0, D1, D2, D3]
    assert growth.assets["B"] == [_q("0"), _q("0"), _q("0.20"), _q("0.20")]
    # D1: 0.5 x 1.10 + 0.5 x 1.00 - 1; D2 on: 0.5 x 1.10 + 0.5 x 1.20 - 1.
    assert growth.mix == [_q("0"), _q("0.05"), _q("0.15"), _q("0.15")]
    assert result.mix.periods[Period.MAX].return_pct == _q("0.15")


def test_a_usd_asset_with_a_flat_price_gains_what_the_dollar_gains():
    # fx is USD per 1 EUR. 1.10 -> 1.00 means a dollar now buys more euros: 1.10 / 1.00 - 1.
    usd = _asset(
        "US",
        "1",
        [(D0, None), (D1, "0"), (D2, "0")],
        fx=[(D0, "1.10"), (D1, "1.05"), (D2, "1.00")],
    )

    result = compute_backtest([usd], growth_period=Period.MAX)

    assert _row(result, "US").periods[Period.MAX].return_pct == _q("0.10")


def test_fx_moves_count_on_days_the_asset_did_not_trade():
    # US doesn't trade on D1, but the dollar moves that day; its EUR value moves with it.
    usd = _asset("US", "0.5", [(D0, None), (D2, "0")], fx=[(D0, "1.10"), (D1, "1.00")])
    eur = _asset("EU", "0.5", [(D0, None), (D1, "0"), (D2, "0")])

    result = compute_backtest([usd, eur], growth_period=Period.MAX)

    # The latest rate on or before each date: D2 has none of its own, so D1's 1.00 applies.
    assert result.growth.assets["US"] == [_q("0"), _q("0.10"), _q("0.10")]


def test_an_asset_without_an_fx_series_is_left_out_and_the_rest_reweighted():
    usd = _asset("US", "0.7", [(D0, None), (D1, "0.50")], fx=[])
    eur = _asset("EU", "0.3", [(D0, None), (D1, "0.10")])

    result = compute_backtest([usd, eur], growth_period=Period.MAX)

    assert [(u.ticker, u.reason) for u in result.unavailable] == [("US", "no FX rate")]
    assert [row.ticker for row in result.assets] == ["EU"]
    assert _row(result, "EU").weight == Decimal("1")
    assert result.mix.periods[Period.MAX].return_pct == _q("0.10")


def test_an_asset_with_no_prices_is_left_out_as_having_no_history_yet():
    new = _asset("NEW", "0.5", [])
    eur = _asset("EU", "0.5", [(D0, None), (D1, "0.10")])

    result = compute_backtest([new, eur], growth_period=Period.MAX)

    assert [(u.ticker, u.reason) for u in result.unavailable] == [("NEW", "no history yet")]
    assert result.mix.periods[Period.MAX].return_pct == _q("0.10")


def test_an_asset_whose_fx_series_starts_late_starts_there():
    # US has prices from D0 but no rate before D2, so its usable history, and its own Max, start
    # on D2. The mix's Max starts on EU's first day, D0, and US joins it on D2.
    usd = _asset(
        "US",
        "0.5",
        [(D0, None), (D1, "0.50"), (D2, "0"), (D3, "0.10")],
        fx=[(D2, "1.00"), (D3, "1.00")],
    )
    eur = _asset("EU", "0.5", [(D0, None), (D1, "0"), (D2, "0"), (D3, "0.20")])

    result = compute_backtest([usd, eur], growth_period=Period.MAX)

    assert _row(result, "US").periods[Period.MAX].start == D2
    assert _row(result, "US").periods[Period.MAX].return_pct == _q("0.10")
    # EU alone is flat to D2; rebalanced 50/50 there: 0.5 x 1.10 + 0.5 x 1.20 - 1.
    assert result.mix.periods[Period.MAX].start == D0
    assert result.mix.periods[Period.MAX].return_pct == _q("0.15")


def test_a_null_return_is_bridged_as_a_flat_day_and_counted():
    # D0's NULL is the series' first day, not a gap. D2's is a gap: 1.10 x 1 x 1.10 - 1.
    asset = _asset("A", "1", [(D0, None), (D1, "0.10"), (D2, None), (D3, "0.10")])

    result = compute_backtest([asset], growth_period=Period.MAX)

    period = _row(result, "A").periods[Period.MAX]
    assert period.return_pct == _q("0.21")
    assert period.gaps == 1
    assert result.mix.periods[Period.MAX].gaps == 1


def test_a_gap_before_a_period_starts_is_not_counted_in_it():
    asset = _asset(
        "A",
        "1",
        [
            (date(2026, 1, 30), None),
            (date(2026, 2, 10), None),
            (date(2026, 2, 27), "0.10"),
            (date(2026, 3, 31), "0.10"),
        ],
    )

    result = compute_backtest([asset], growth_period=Period.MAX)

    assert _row(result, "A").periods[Period.M1].gaps == 0
    assert _row(result, "A").periods[Period.MAX].gaps == 1


def test_a_period_longer_than_an_assets_history_leaves_it_out_of_the_mix():
    long = _asset(
        "LONG",
        "0.5",
        [(date(2025, 12, 31), None), (date(2026, 2, 27), "0.10"), (date(2026, 3, 31), "0.10")],
    )
    short = _asset("SHORT", "0.5", [(date(2026, 2, 15), None), (date(2026, 3, 31), "0.20")])

    result = compute_backtest([long, short], growth_period=Period.MAX)

    # 1M starts 02-28: both cover it. LONG's base is 02-27, SHORT's is its first day, 02-15.
    assert _row(result, "LONG").periods[Period.M1].return_pct == _q("0.10")
    assert _row(result, "SHORT").periods[Period.M1].return_pct == _q("0.20")
    assert result.mix.periods[Period.M1].return_pct == _q("0.15")
    assert result.mix.periods[Period.M1].left_out == []
    # 3M starts 2025-12-31: LONG covers it (1.10 x 1.10 - 1), SHORT doesn't, so the mix is LONG
    # alone and names SHORT.
    assert _row(result, "LONG").periods[Period.M3].return_pct == _q("0.21")
    assert _row(result, "SHORT").periods[Period.M3].covered is False
    assert result.mix.periods[Period.M3].covered is True
    assert result.mix.periods[Period.M3].return_pct == _q("0.21")
    assert result.mix.periods[Period.M3].left_out == ["SHORT"]
    # An asset's own row never leaves anything out.
    assert _row(result, "LONG").periods[Period.M3].left_out == []


def test_the_mix_reweights_the_assets_that_cover_a_period_to_100_percent():
    days = [date(2025, 12, 31), date(2026, 2, 27), date(2026, 3, 31)]
    long = _asset("LONG", "0.5", list(zip(days, [None, "0.10", "0.10"])))
    flat = _asset("FLAT", "0.3", list(zip(days, [None, "0", "0"])))
    short = _asset("SHORT", "0.2", [(date(2026, 2, 15), None), (date(2026, 3, 31), "0.20")])

    result = compute_backtest([long, flat, short], growth_period=Period.M3)

    # 3M: SHORT is left out, so LONG and FLAT hold 0.5/0.8 and 0.3/0.8:
    # 0.625 x 1.21 + 0.375 x 1 - 1 = 0.13125.
    assert result.mix.periods[Period.M3].return_pct == _q("0.13125")
    assert result.mix.periods[Period.M3].left_out == ["SHORT"]
    # The rows still report the weights given over every available asset.
    assert [row.weight for row in result.assets] == [
        Decimal("0.5"),
        Decimal("0.3"),
        Decimal("0.2"),
    ]


def test_the_mix_has_no_figure_only_when_no_asset_covers_the_period():
    # Both start 03-15, after 1M's start (02-28).
    a = _asset("A", "0.5", [(date(2026, 3, 15), None), (date(2026, 3, 31), "0.10")])
    b = _asset("B", "0.5", [(date(2026, 3, 15), None), (date(2026, 3, 31), "0.30")])

    result = compute_backtest([b, a], growth_period=Period.M1)

    assert result.mix.periods[Period.M1].covered is False
    assert result.mix.periods[Period.M1].return_pct is None
    assert result.mix.periods[Period.M1].left_out == ["B", "A"]
    assert result.growth.mix is None
    assert result.growth.left_out == ["B", "A"]
    # Max covers both: 0.5 x 1.10 + 0.5 x 1.30 - 1.
    assert result.mix.periods[Period.MAX].return_pct == _q("0.20")


def test_each_assets_max_is_its_own_history_and_the_mix_starts_at_the_earliest():
    long = _asset(
        "LONG",
        "0.5",
        [(date(2025, 12, 31), None), (date(2026, 2, 27), "0.10"), (date(2026, 3, 31), "0.10")],
    )
    short = _asset("SHORT", "0.5", [(date(2026, 2, 15), None), (date(2026, 3, 31), "0.20")])

    result = compute_backtest([long, short], growth_period=Period.MAX)

    starts = {row.ticker: row.periods[Period.MAX].start for row in result.assets}
    assert starts == {"LONG": date(2025, 12, 31), "SHORT": date(2026, 2, 15)}
    assert _row(result, "LONG").periods[Period.MAX].return_pct == _q("0.21")
    assert _row(result, "SHORT").periods[Period.MAX].return_pct == _q("0.20")
    # The mix holds LONG alone until 02-15, when it still stands at 1.00. SHORT joins there and
    # the mix is split 50/50 again: 0.5 x 1.21 + 0.5 x 1.20 - 1.
    assert result.mix.periods[Period.MAX].start == date(2025, 12, 31)
    assert result.mix.periods[Period.MAX].return_pct == _q("0.205")
    assert result.mix.periods[Period.MAX].left_out == []


def test_an_asset_joining_max_late_rebalances_the_mix_to_its_target_weights():
    # A and B from D0, C from D2; weights 1/1/2.
    a = _asset("A", "1", [(D0, None), (D1, "0.10"), (D2, "0"), (D3, "0.20")])
    b = _asset("B", "1", [(D0, None), (D1, "-0.10"), (D2, "0"), (D3, "0")])
    c = _asset("C", "2", [(D2, None), (D3, "0.10")])

    result = compute_backtest([a, b, c], growth_period=Period.MAX)

    # Up to D2, A and B 50/50: 0.5 x 1.10 + 0.5 x 0.90 = 1.00. On D2 C joins, and the 1.00 is
    # split 25/25/50: D3 is 0.25 x 1.20 + 0.25 x 1.00 + 0.5 x 1.10 = 1.10. Keeping A and B's
    # drifted 55/45 and buying C beside them would give 0.5 x (0.55 x 1.2 + 0.45) + 0.55 = 1.105.
    assert result.mix.periods[Period.MAX].start == D0
    assert result.mix.periods[Period.MAX].return_pct == _q("0.10")
    assert _row(result, "C").periods[Period.MAX].start == D2
    assert _row(result, "C").periods[Period.MAX].return_pct == _q("0.10")


def test_a_late_assets_max_line_is_empty_until_it_starts():
    a = _asset("A", "1", [(D0, None), (D1, "0.10"), (D2, "0"), (D3, "0.20")])
    b = _asset("B", "1", [(D0, None), (D1, "-0.10"), (D2, "0"), (D3, "0")])
    c = _asset("C", "2", [(D2, None), (D3, "0.10")])

    result = compute_backtest([a, b, c], growth_period=Period.MAX)

    growth = result.growth
    assert growth.dates == [D0, D1, D2, D3]
    assert growth.assets["C"] == [None, None, _q("0"), _q("0.10")]
    assert growth.assets["A"] == [_q("0"), _q("0.10"), _q("0.10"), _q("0.32")]
    # D1 and D2: 0.5 x 1.10 + 0.5 x 0.90 - 1; D3 as in the test above.
    assert growth.mix == [_q("0"), _q("0"), _q("0"), _q("0.10")]
    assert growth.left_out == []


def test_a_period_starting_on_a_weekend_uses_the_close_before_it():
    # E is Monday 2026-03-30, so 1M starts Saturday 02-28. The base is Friday 02-27's close, so
    # Monday 03-02's return is inside the period: 1.10 x 1.10 - 1, not 1.10 - 1.
    asset = _asset(
        "A",
        "1",
        [
            (date(2026, 1, 30), None),
            (date(2026, 2, 27), "0.50"),
            (date(2026, 3, 2), "0.10"),
            (date(2026, 3, 30), "0.10"),
        ],
    )

    result = compute_backtest([asset], growth_period=Period.MAX)

    assert _row(result, "A").periods[Period.M1].start == date(2026, 2, 28)
    assert _row(result, "A").periods[Period.M1].return_pct == _q("0.21")


@pytest.mark.parametrize(
    "end, period, start",
    [
        (date(2026, 3, 31), Period.M1, date(2026, 2, 28)),
        (date(2024, 3, 31), Period.M1, date(2024, 2, 29)),
        (date(2024, 2, 29), Period.Y1, date(2023, 2, 28)),
        (date(2026, 8, 31), Period.M6, date(2026, 2, 28)),
        (date(2026, 1, 15), Period.Y5, date(2021, 1, 15)),
    ],
)
def test_period_starts_clamp_the_day_of_the_month(end, period, start):
    asset = _asset("A", "1", [(date(2020, 1, 2), None), (end, "0")])

    result = compute_backtest([asset], growth_period=Period.MAX)

    assert _row(result, "A").periods[period].start == start


def test_every_period_ends_on_the_last_day_all_assets_have():
    # A has D3, B stops at D2, so everything ends on D2 and A's D3 return is left out.
    a = _asset("A", "0.5", [(D0, None), (D1, "0.10"), (D2, "0"), (D3, "0.50")])
    b = _asset("B", "0.5", [(D0, None), (D1, "0"), (D2, "0")])

    result = compute_backtest([a, b], growth_period=Period.MAX)

    assert result.end_date == D2
    assert _row(result, "A").periods[Period.MAX].return_pct == _q("0.10")
    assert result.growth.dates == [D0, D1, D2]


def test_an_asset_starting_after_the_common_end_is_left_out_even_of_max():
    # A stops on D1, B starts on D2: everything ends on D1, before B has a single day.
    a = _asset("A", "0.5", [(D0, None), (D1, "0.10")])
    b = _asset("B", "0.5", [(D2, None), (D3, "0.10")])

    result = compute_backtest([a, b], growth_period=Period.MAX)

    assert _row(result, "B").periods[Period.MAX].covered is False
    assert _row(result, "A").periods[Period.MAX].return_pct == _q("0.10")
    assert result.mix.periods[Period.MAX].return_pct == _q("0.10")
    assert result.mix.periods[Period.MAX].left_out == ["B"]
    assert result.growth.dates == [D0, D1]
    assert result.growth.mix == [_q("0"), _q("0.10")]
    assert result.growth.assets["B"] is None
    assert result.growth.left_out == ["B"]


def test_weights_that_do_not_add_up_are_normalized():
    # Normalizing was agreed in LB-7: web shows the total and the weights actually used.
    a = _asset("A", "30", [(D0, None), (D1, "0.10")])
    b = _asset("B", "30", [(D0, None), (D1, "0.30")])

    result = compute_backtest([a, b], growth_period=Period.MAX)

    assert [row.weight for row in result.assets] == [Decimal("0.5"), Decimal("0.5")]
    assert result.mix.weight == Decimal("1")
    assert result.mix.periods[Period.MAX].return_pct == _q("0.20")


def test_the_growth_series_covers_the_requested_period_only():
    asset = _asset(
        "A",
        "1",
        [(date(2026, 1, 30), None), (date(2026, 2, 27), "0.10"), (date(2026, 3, 31), "-0.10")],
    )

    result = compute_backtest([asset], growth_period=Period.M1)

    # It starts on the period's start date at 0, then one point per trading day after it.
    assert result.growth.period == Period.M1
    assert result.growth.dates == [date(2026, 2, 28), date(2026, 3, 31)]
    assert result.growth.assets["A"] == [_q("0"), _q("-0.10")]
    assert result.growth.mix == [_q("0"), _q("-0.10")]


def test_the_growth_series_has_no_line_for_an_asset_that_does_not_cover_the_period():
    long = _asset(
        "LONG",
        "0.5",
        [(date(2025, 12, 31), None), (date(2026, 2, 27), "0.10"), (date(2026, 3, 31), "0.10")],
    )
    short = _asset("SHORT", "0.5", [(date(2026, 2, 15), None), (date(2026, 3, 31), "0.20")])

    result = compute_backtest([long, short], growth_period=Period.M3)

    assert result.growth.assets["SHORT"] is None
    # SHORT's first day, 02-15, is still a trading day on the chart; LONG stands at 1.00 there.
    assert result.growth.dates == [
        date(2025, 12, 31),
        date(2026, 2, 15),
        date(2026, 2, 27),
        date(2026, 3, 31),
    ]
    assert result.growth.assets["LONG"] == [_q("0"), _q("0"), _q("0.10"), _q("0.21")]
    # The mix is LONG alone, and names SHORT.
    assert result.growth.mix == result.growth.assets["LONG"]
    assert result.growth.left_out == ["SHORT"]


def test_with_nothing_available_there_is_no_result_to_show():
    result = compute_backtest(
        [_asset("NEW", "1", []), _asset("US", "1", [(D0, None)], fx=[])],
        growth_period=Period.MAX,
    )

    assert result.end_date is None
    assert result.mix is None
    assert result.assets == []
    assert result.growth is None
    assert [u.ticker for u in result.unavailable] == ["NEW", "US"]


@pytest.mark.parametrize(
    "assets",
    [
        [],
        [_asset("A", "1", [(D0, None)]), _asset("A", "1", [(D0, None)])],
        [_asset("A", "0", [(D0, None)])],
        [_asset("A", "-1", [(D0, None)])],
    ],
    ids=["empty", "duplicate", "zero-weight", "negative-weight"],
)
def test_an_invalid_mix_is_refused(assets):
    with pytest.raises(ValueError):
        compute_backtest(assets, growth_period=Period.MAX)
