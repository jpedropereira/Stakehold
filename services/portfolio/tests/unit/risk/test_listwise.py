"""risk/listwise.py: R12's listwise weekly window, the only panel portfolio maths takes (R13).

Calendar: 2020-01-03 is a Friday. Weekly fixtures are consecutive Fridays after their base.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.listwise import listwise
from portfolio.risk.types import Frequency, ReturnSeries, Window

FRIDAY = date(2020, 1, 3)


def _weekly(values, base=FRIDAY):
    dates = tuple(base + timedelta(weeks=k) for k in range(1, len(values) + 1))
    return ReturnSeries(Frequency.WEEKLY, base, dates, tuple(values))


def _fridays(k):
    return FRIDAY + timedelta(weeks=k)


def test_keeps_every_week_when_nothing_is_null():
    panel = listwise(
        {"A": _weekly([0.01, 0.02, 0.03]), "B": _weekly([0.04, 0.05, 0.06])},
        Window("Max", FRIDAY, _fridays(3)),
    )
    assert panel.keys == ("A", "B")
    assert panel.dates == (_fridays(1), _fridays(2), _fridays(3))
    np.testing.assert_array_equal(panel.returns, [[0.01, 0.04], [0.02, 0.05], [0.03, 0.06]])
    assert (panel.n_obs, panel.n_gaps) == (3, 0)
    assert panel.limited_by == ()
    assert panel.window == Window("Max", FRIDAY, _fridays(3))


def test_a_null_week_in_one_holding_is_dropped_for_all_and_counted():
    panel = listwise(
        {"A": _weekly([0.01, None, 0.03]), "B": _weekly([0.04, 0.05, None])},
        Window("Max", FRIDAY, _fridays(3)),
    )
    assert panel.dates == (_fridays(1),)
    np.testing.assert_array_equal(panel.returns, [[0.01, 0.04]])
    assert (panel.n_obs, panel.n_gaps) == (1, 2)


def test_a_late_holding_shortens_the_window_and_is_named():
    late = _fridays(2)
    panel = listwise(
        {"A": _weekly([0.01, 0.02, 0.03, 0.04]), "B": _weekly([0.05, 0.06], base=late)},
        Window("3Y", FRIDAY, _fridays(4)),
    )
    assert panel.window == Window("3Y", late, _fridays(4))
    assert panel.dates == (_fridays(3), _fridays(4))
    np.testing.assert_array_equal(panel.returns, [[0.03, 0.05], [0.04, 0.06]])
    assert panel.limited_by == ("B",)
    assert panel.n_gaps == 0


def test_holdings_tied_for_the_latest_start_are_all_named():
    late = _fridays(1)
    panel = listwise(
        {
            "A": _weekly([0.01, 0.02, 0.03]),
            "B": _weekly([0.04, 0.05], base=late),
            "C": _weekly([0.06, 0.07], base=late),
        },
        Window("3Y", FRIDAY, _fridays(3)),
    )
    assert panel.limited_by == ("B", "C")


def test_a_window_inside_every_history_names_no_holding():
    panel = listwise(
        {"A": _weekly([0.01, 0.02, 0.03]), "B": _weekly([0.04, 0.05, 0.06])},
        Window("1Y", _fridays(1), _fridays(3)),
    )
    assert panel.limited_by == ()
    assert panel.dates == (_fridays(2), _fridays(3))


def test_the_window_shrinks_to_whole_weeks():
    # A Wednesday start and a Tuesday end shrink to the Fridays inside them.
    panel = listwise(
        {"A": _weekly([0.01, 0.02, 0.03, 0.04])},
        Window("Max", FRIDAY - timedelta(days=2), _fridays(3) + timedelta(days=4)),
    )
    assert panel.window == Window("Max", FRIDAY, _fridays(3))
    assert panel.dates == (_fridays(1), _fridays(2), _fridays(3))


def test_the_end_is_clipped_to_the_shortest_history():
    panel = listwise(
        {"A": _weekly([0.01, 0.02, 0.03]), "B": _weekly([0.04, 0.05])},
        Window("Max", FRIDAY, _fridays(3)),
    )
    assert panel.window.end == _fridays(2)
    assert panel.dates == (_fridays(1), _fridays(2))


def test_disjoint_histories_give_an_empty_panel():
    panel = listwise(
        {"A": _weekly([0.01, 0.02]), "B": _weekly([0.03, 0.04], base=_fridays(5))},
        Window("Max", FRIDAY, _fridays(7)),
    )
    assert panel.dates == ()
    assert panel.returns.shape == (0, 2)
    assert (panel.n_obs, panel.n_gaps) == (0, 0)


def test_a_window_with_no_whole_week_gives_an_empty_panel():
    panel = listwise(
        {"A": _weekly([0.01, 0.02])},
        Window("Max", FRIDAY + timedelta(days=1), FRIDAY + timedelta(days=5)),
    )
    assert panel.dates == ()


def test_suspect_rows_inside_the_window_are_recorded():
    panel = listwise(
        {"A": _weekly([0.01, 1.5, 0.03]), "B": _weekly([0.04, 0.05, 0.06])},
        Window("Max", FRIDAY, _fridays(3)),
    )
    assert panel.has_suspect_rows


def test_no_suspect_rows():
    panel = listwise({"A": _weekly([0.01, 0.02])}, Window("Max", FRIDAY, _fridays(2)))
    assert not panel.has_suspect_rows


def test_a_daily_series_is_refused():
    daily = ReturnSeries(Frequency.DAILY, FRIDAY, (FRIDAY + timedelta(days=3),), (0.01,))
    with pytest.raises(ValueError, match="weekly"):
        listwise({"A": daily}, Window("Max", FRIDAY, _fridays(1)))


def test_no_holdings_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        listwise({}, Window("Max", FRIDAY, _fridays(1)))
