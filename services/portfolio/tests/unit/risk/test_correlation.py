"""risk/correlation.py: Pearson on weekly returns (spec §7.1). Pairwise for display, each cell
over its own overlap with its own n_obs and start date; listwise for maths, over R12's window
(only the weeks in which every series has a return), and the two kept apart by type (R13).

Calendar: 2020-01-03 is a Friday, F0. A weekly fixture's week k (from 0) is dated F0 + (k + 1)
weeks, so a series starting at week s has base date F0 + s weeks. The patterns are built so
each ρ is exact by hand, over a whole number of their 4-week cycles:
- P: +1%, −1%, +1%, −1%, ... (by week index)
- Q: +1%, +1%, −1%, −1%, ...
Both have mean 0 over a cycle, and P·Q is +, −, −, + over one, so ρ(P, Q) = 0. Any positive
multiple of a series plus a constant has ρ = 1 with it; its negative has ρ = −1.
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from portfolio.datastore_client.base import FxPoint, PricePoint
from portfolio.eur_returns.calculator import EurReturns, eur_daily_returns
from portfolio.risk.correlation import (
    ListwiseCorrelation,
    ListwisePanel,
    listwise_correlation,
    listwise_panel,
    pairwise_correlation,
)
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window
from portfolio.risk.weekly import to_weekly

F0 = date(2020, 1, 3)
WEEK = timedelta(days=7)


def _p(k):
    return 0.01 if k % 2 == 0 else -0.01


def _q(k):
    return 0.01 if k % 4 in (0, 1) else -0.01


def _weekly(start_week, values):
    """A weekly series whose first return is week `start_week`."""
    base = F0 + start_week * WEEK
    dates = tuple(base + (i + 1) * WEEK for i in range(len(values)))
    return ReturnSeries(Frequency.WEEKLY, base, dates, tuple(values))


def _pattern(fn, start_week, end_week, scale=1.0, shift=0.0):
    return _weekly(start_week, [scale * fn(k) + shift for k in range(start_week, end_week)])


def _friday(week):
    """The Friday that week `week` ends on."""
    return F0 + (week + 1) * WEEK


def _staggered():
    """A from week 0, B from week 20, C from week 40, all to week 160.
    - B is A for weeks 20-39 and Q after, so over 40-159 ρ(A, B) = 0, and over 20-159 it isn't.
    - C is −A: ρ(A, C) = −1 and ρ(B, C) = ρ(Q, −P) = 0 over 40-159."""
    a = _pattern(_p, 0, 160)
    b = _weekly(20, [_p(k) if k < 40 else _q(k) for k in range(20, 160)])
    c = _pattern(_p, 40, 160, scale=-1.0)
    return {"A": a, "B": b, "C": c}


FULL = Window("Max", F0, _friday(159))


# --- pairwise ---------------------------------------------------------------------------------


def test_pairwise_refuses_a_daily_series():
    daily = ReturnSeries(Frequency.DAILY, F0, (F0 + timedelta(days=3),), (0.01,))
    with pytest.raises(ValueError, match="weekly"):
        pairwise_correlation({"A": _pattern(_p, 0, 110), "D": daily}, FULL)


def test_pairwise_cells_each_use_their_own_overlap():
    result = pairwise_correlation(_staggered(), FULL)

    assert result.labels == ("A", "B", "C")
    ab, ac, bc = result.cell("A", "B"), result.cell("A", "C"), result.cell("B", "C")
    # A-B overlap weeks 20-159: 140 obs, measured from week 20's base.
    assert (ab.n_obs, ab.window_start, ab.window_end) == (140, F0 + 20 * WEEK, _friday(159))
    # A-C and B-C overlap weeks 40-159: 120 obs.
    assert (ac.n_obs, ac.window_start) == (120, F0 + 40 * WEEK)
    assert (bc.n_obs, bc.window_start) == (120, F0 + 40 * WEEK)
    assert ac.value == pytest.approx(-1.0)
    assert bc.value == pytest.approx(0.0, abs=1e-12)
    # Over 20-159, B is A for 20 weeks and orthogonal to it for 120: not 0.
    assert ab.value == pytest.approx(20 / 140)
    for cell in (ab, ac, bc):
        assert cell.frequency is Frequency.WEEKLY
        assert cell.q == 52
        assert cell.n_gaps == 0
        assert cell.flags == frozenset()


def test_pairwise_cells_are_symmetric_and_the_diagonal_is_one():
    result = pairwise_correlation(_staggered(), FULL)

    assert result.cell("B", "A") == result.cell("A", "B")
    diagonal = result.cell("A", "A")
    assert diagonal.value == pytest.approx(1.0)
    assert diagonal.n_obs == 160


def test_pairwise_unknown_label_raises():
    result = pairwise_correlation(_staggered(), FULL)
    with pytest.raises(KeyError):
        result.cell("A", "Z")


def test_a_null_week_touches_only_that_series_cells():
    series = _staggered()
    a = series["A"]
    values = list(a.values)
    values[50] = None
    series["A"] = ReturnSeries(a.frequency, a.base_date, a.dates, tuple(values))

    result = pairwise_correlation(series, FULL)

    assert (result.cell("A", "A").n_obs, result.cell("A", "A").n_gaps) == (159, 1)
    assert (result.cell("A", "B").n_obs, result.cell("A", "B").n_gaps) == (139, 1)
    assert (result.cell("A", "C").n_obs, result.cell("A", "C").n_gaps) == (119, 1)
    assert (result.cell("B", "C").n_obs, result.cell("B", "C").n_gaps) == (120, 0)


def test_pairwise_cell_is_restricted_to_the_window():
    window = Window("3Y", _friday(59), _friday(159))  # weeks 60-159
    result = pairwise_correlation(_staggered(), window)

    ab = result.cell("A", "B")
    assert (ab.n_obs, ab.window_start, ab.window_end) == (100, _friday(59), _friday(159))
    assert ab.value is None  # 100 < 104


def test_pairwise_window_shrinks_to_whole_weeks():
    # A Wednesday start moves to the Friday after it; a Wednesday end to the Friday before.
    window = Window("w", _friday(0) - timedelta(days=2), _friday(120) - timedelta(days=2))
    result = pairwise_correlation({"A": _pattern(_p, 0, 160), "B": _pattern(_q, 0, 160)}, window)

    cell = result.cell("A", "B")
    # Weeks 1-119: base is week 0's Friday.
    assert (cell.window_start, cell.window_end, cell.n_obs) == (_friday(0), _friday(119), 119)


def test_pairwise_minimum_is_104_overlapping_weeks():
    a = _pattern(_p, 0, 104)
    b = _pattern(_p, 1, 104, scale=2.0, shift=0.001)  # 103 weeks overlapping A

    short = pairwise_correlation({"A": a, "B": b}, FULL).cell("A", "B")
    assert short.value is None
    assert short.n_obs == 103
    assert short.reason == "insufficient history (have 103, need 104)"
    assert short.flags == frozenset({Flag.INSUFFICIENT_HISTORY})

    b = _pattern(_p, 0, 104, scale=2.0, shift=0.001)
    enough = pairwise_correlation({"A": a, "B": b}, FULL).cell("A", "B")
    assert enough.n_obs == 104
    assert enough.value == pytest.approx(1.0)
    assert enough.reason is None


def test_no_overlap_is_greyed_with_zero_obs():
    result = pairwise_correlation(
        {"A": _pattern(_p, 0, 120), "B": _pattern(_p, 130, 250)}, Window("Max", F0, _friday(249))
    )

    cell = result.cell("A", "B")
    assert cell.value is None
    assert cell.n_obs == 0
    assert cell.reason == "insufficient history (have 0, need 104)"


def test_a_constant_series_is_greyed_not_inf_or_zero():
    flat = _weekly(0, [0.01] * 120)
    result = pairwise_correlation({"A": _pattern(_p, 0, 120), "F": flat}, FULL)

    cell = result.cell("A", "F")
    assert cell.value is None
    assert cell.reason == "no variation in returns"
    assert result.cell("F", "F").value is None


def test_pairwise_flags_data_gaps_over_two_percent():
    values = [_p(k) for k in range(107)]
    for k in (10, 20, 30):  # 3 gaps on 104 obs: 3 > 2% of 107
        values[k] = None
    result = pairwise_correlation({"A": _weekly(0, values), "B": _pattern(_q, 0, 107)}, FULL).cell(
        "A", "B"
    )

    assert (result.n_obs, result.n_gaps) == (104, 3)
    assert Flag.DATA_GAPS in result.flags
    assert result.value is not None


def test_pairwise_flags_suspect_rows_from_either_side():
    values = [_p(k) for k in range(120)]
    values[5] = 1.5
    result = pairwise_correlation({"A": _pattern(_q, 0, 120), "S": _weekly(0, values)}, FULL).cell(
        "A", "S"
    )

    assert Flag.HAS_SUSPECT_ROWS in result.flags


def test_pairwise_result_carries_no_matrix():
    # R13: a pairwise matrix can be non-PSD, so it never offers an array to do maths with.
    result = pairwise_correlation(_staggered(), FULL)
    assert not any(isinstance(v, np.ndarray) for v in vars(result).values())


# --- listwise ---------------------------------------------------------------------------------


def test_listwise_panel_starts_at_the_latest_start_and_names_it():
    panel = listwise_panel(_staggered(), FULL)

    assert isinstance(panel, ListwisePanel)
    assert panel.labels == ("A", "B", "C")
    assert panel.limiting == "C"
    assert (panel.window.start, panel.window.end) == (F0 + 40 * WEEK, _friday(159))
    assert panel.dates == tuple(_friday(k) for k in range(40, 160))
    assert panel.returns.shape == (120, 3)
    assert (panel.n_obs, panel.n_gaps) == (120, 0)
    assert panel.returns[0].tolist() == [_p(40), _q(40), -_p(40)]


def test_listwise_panel_names_no_limit_when_every_series_covers_the_window():
    series = {"A": _pattern(_p, 0, 160), "B": _pattern(_q, 0, 160)}
    panel = listwise_panel(series, Window("3Y", _friday(9), _friday(159)))

    assert panel.limiting is None
    assert panel.window.start == _friday(9)
    assert panel.n_obs == 150


def test_listwise_panel_ends_at_the_earliest_last_date():
    series = {"A": _pattern(_p, 0, 160), "B": _pattern(_q, 0, 140)}
    panel = listwise_panel(series, FULL)

    assert panel.window.end == _friday(139)
    assert panel.n_obs == 140


def test_listwise_panel_drops_a_week_with_a_null_in_any_series():
    series = _staggered()
    a = series["A"]
    values = list(a.values)
    values[50] = None
    series["A"] = ReturnSeries(a.frequency, a.base_date, a.dates, tuple(values))

    panel = listwise_panel(series, FULL)

    assert (panel.n_obs, panel.n_gaps) == (119, 1)
    assert _friday(50) not in panel.dates
    assert not np.isnan(panel.returns).any()


def test_listwise_panel_refuses_a_daily_series():
    daily = ReturnSeries(Frequency.DAILY, F0, (F0 + timedelta(days=3),), (0.01,))
    with pytest.raises(ValueError, match="weekly"):
        listwise_panel({"A": _pattern(_p, 0, 110), "D": daily}, FULL)


def test_listwise_panel_returns_are_read_only():
    panel = listwise_panel(_staggered(), FULL)
    with pytest.raises(ValueError):
        panel.returns[0, 0] = 0.5


def test_listwise_correlation_uses_the_common_weeks_only():
    result = listwise_correlation(listwise_panel(_staggered(), FULL))

    assert isinstance(result, ListwiseCorrelation)
    assert result.reason is None
    assert (result.n_obs, result.limiting) == (120, "C")
    # Over weeks 40-159 B is Q throughout, so ρ(A, B) = 0, where the pairwise cell said 20/140.
    expected = np.array([[1.0, 0.0, -1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 1.0]])
    np.testing.assert_allclose(result.matrix, expected, atol=1e-12)
    with pytest.raises(ValueError):
        result.matrix[0, 1] = 0.5


def test_listwise_correlation_minimum_is_104_common_weeks():
    series = {"A": _pattern(_p, 0, 160), "B": _pattern(_q, 57, 160)}  # 103 common weeks
    result = listwise_correlation(listwise_panel(series, FULL))

    assert result.matrix is None
    assert result.reason == "insufficient history (have 103, need 104)"
    assert Flag.INSUFFICIENT_HISTORY in result.flags
    assert result.limiting == "B"


def test_listwise_correlation_names_a_constant_series():
    series = {"A": _pattern(_p, 0, 120), "F": _weekly(0, [0.01] * 120)}
    result = listwise_correlation(listwise_panel(series, FULL))

    assert result.matrix is None
    assert result.reason == "no variation in returns (F)"


def test_listwise_without_overlap_is_greyed_not_raised():
    series = {"A": _pattern(_p, 0, 120), "B": _pattern(_q, 130, 250)}
    panel = listwise_panel(series, Window("Max", F0, _friday(249)))

    assert panel.window is None
    assert panel.n_obs == 0
    assert panel.returns.shape == (0, 2)
    result = listwise_correlation(panel)
    assert result.matrix is None
    assert result.reason == "insufficient history (have 0, need 104)"


def test_listwise_correlation_flags_data_gaps():
    values = [_p(k) for k in range(107)]
    for k in (10, 20, 30):
        values[k] = None
    series = {"A": _weekly(0, values), "B": _pattern(_q, 0, 107)}
    result = listwise_correlation(listwise_panel(series, FULL))

    assert (result.n_obs, result.n_gaps) == (104, 3)
    assert Flag.DATA_GAPS in result.flags


def test_listwise_correlation_refuses_a_pairwise_matrix():
    # R13: a pairwise matrix must never reach portfolio maths.
    pairwise = pairwise_correlation(_staggered(), FULL)
    with pytest.raises(TypeError, match="ListwisePanel"):
        listwise_correlation(pairwise)
    with pytest.raises(TypeError, match="ListwisePanel"):
        listwise_correlation(np.eye(3))


# --- EUR vs local -----------------------------------------------------------------------------


def _usd_returns(local, fx_moves, days):
    """A USD asset's EurReturns from daily local returns and the EURUSD path (USD per EUR)."""
    prices = [PricePoint(days[0], Decimal("100"), None)]
    prices += [
        PricePoint(day, Decimal("100"), Decimal(f"{r:.10f}")) for day, r in zip(days[1:], local)
    ]
    rate, fx = 1.10, [FxPoint(days[0], Decimal("1.10"))]
    for day, move in zip(days[1:], fx_moves):
        rate *= 1 + move
        fx.append(FxPoint(day, Decimal(f"{rate:.10f}")))
    result = eur_daily_returns(prices, fx)
    assert isinstance(result, EurReturns)
    return result


def _float_series(anchor, dates, values):
    return ReturnSeries(
        Frequency.DAILY,
        anchor,
        tuple(dates),
        tuple(None if v is None else float(v) for v in values),
    )


def test_eur_raises_two_usd_assets_correlation_over_local():
    # Two USD assets with independent local returns, both converted through one EURUSD path:
    # in EUR they share the dollar's moves, which is real risk for a EUR investor (§7.1).
    days, day = [], F0
    while len(days) < 3 * 261:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    rng = np.random.default_rng(16)
    n = len(days) - 1
    local_x, local_y = rng.normal(0, 0.01, n), rng.normal(0, 0.01, n)
    fx_moves = rng.normal(0, 0.006, n)

    x = _usd_returns(local_x, fx_moves, days)
    y = _usd_returns(local_y, fx_moves, days)
    window = Window("Max", F0, days[-1])

    def cell(field):
        series = {
            name: to_weekly(_float_series(r.anchor, r.dates, getattr(r, field)))
            for name, r in (("X", x), ("Y", y))
        }
        return pairwise_correlation(series, window).cell("X", "Y")

    local, eur = cell("r_local"), cell("r_eur")
    assert local.n_obs == eur.n_obs >= 104
    assert eur.value > local.value + 0.1
