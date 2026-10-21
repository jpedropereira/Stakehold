"""risk/pairing.py: two weekly series paired on the weeks both have inside a window, the overlap
that a pairwise correlation cell (RK-16) and the relative measures (RK-13, spec §5.6) run on.

Calendar: 2020-01-03 is a Friday, F0. A weekly fixture's week k (from 0) is dated F0 + (k + 1)
weeks, so a series starting at week s has base date F0 + s weeks.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.pairing import paired_weeks
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window

F0 = date(2020, 1, 3)
WEEK = timedelta(days=7)


def _weekly(start_week, values):
    base = F0 + start_week * WEEK
    dates = tuple(base + (i + 1) * WEEK for i in range(len(values)))
    return ReturnSeries(Frequency.WEEKLY, base, dates, tuple(values))


def _friday(week):
    return F0 + (week + 1) * WEEK


def _window(start_week, end_week):
    """Returns dated weeks start_week..end_week inclusive."""
    return Window("w", _friday(start_week - 1), _friday(end_week))


def test_pairs_only_the_weeks_both_series_have():
    a = _weekly(0, [0.01 * k for k in range(10)])  # weeks 0-9
    b = _weekly(3, [0.02 * k for k in range(10)])  # weeks 3-12
    pair = paired_weeks(a, b, _window(0, 12))
    assert pair.n_obs == 7
    assert pair.n_gaps == 0
    np.testing.assert_allclose(pair.x, [0.01 * k for k in range(3, 10)])
    np.testing.assert_allclose(pair.y, [0.02 * k for k in range(7)])
    assert (pair.start, pair.end) == (_friday(2), _friday(9))


def test_a_null_on_either_side_is_a_gap_excluded_from_both():
    a = _weekly(0, [0.01, None, 0.03, 0.04])
    b = _weekly(0, [0.05, 0.06, 0.07, None])
    pair = paired_weeks(a, b, _window(0, 3))
    assert (pair.n_obs, pair.n_gaps) == (2, 2)
    np.testing.assert_allclose(pair.x, [0.01, 0.03])
    np.testing.assert_allclose(pair.y, [0.05, 0.07])
    # The span is the shared weeks', gaps included.
    assert (pair.start, pair.end) == (_friday(-1), _friday(3))


def test_only_weeks_inside_the_window_count():
    a = _weekly(0, [0.01] * 20)
    b = _weekly(0, [0.02] * 20)
    pair = paired_weeks(a, b, _window(5, 9))
    assert pair.n_obs == 5
    assert (pair.start, pair.end) == (_friday(4), _friday(9))


def test_the_window_takes_the_whole_weeks_ending_inside_it():
    a = _weekly(0, [0.01] * 20)
    b = _weekly(0, [0.02] * 20)
    # From a Monday to a Wednesday: the week the Monday falls in ends inside the window, so it
    # counts; the week the Wednesday falls in hasn't ended, so the end moves to the Friday before.
    window = Window("w", _friday(4) + timedelta(days=3), _friday(9) + timedelta(days=5))
    pair = paired_weeks(a, b, window)
    assert (pair.start, pair.end) == (_friday(4), _friday(9))
    assert pair.n_obs == 5


def test_no_shared_week_gives_zero_obs_over_the_window():
    a = _weekly(0, [0.01] * 5)
    b = _weekly(10, [0.02] * 5)
    window = _window(0, 14)
    pair = paired_weeks(a, b, window)
    assert (pair.n_obs, pair.n_gaps) == (0, 0)
    assert pair.x.size == pair.y.size == 0
    assert (pair.start, pair.end) == (window.start, window.end)
    assert pair.flags == frozenset()


def test_gaps_over_two_percent_flag_data_gaps():
    values = [0.01] * 49 + [None]  # 1 gap in 50: exactly 2%, within budget
    within = paired_weeks(_weekly(0, values), _weekly(0, [0.02] * 50), _window(0, 49))
    assert Flag.DATA_GAPS not in within.flags
    over = paired_weeks(
        _weekly(0, [0.01] * 48 + [None, None]), _weekly(0, [0.02] * 50), _window(0, 49)
    )
    assert Flag.DATA_GAPS in over.flags


def test_a_kept_move_over_100_percent_on_either_side_is_flagged():
    a = _weekly(0, [0.01, 0.02, 0.03])
    b = _weekly(0, [0.01, 1.5, 0.03])
    assert Flag.HAS_SUSPECT_ROWS in paired_weeks(a, b, _window(0, 2)).flags
    assert Flag.HAS_SUSPECT_ROWS in paired_weeks(b, a, _window(0, 2)).flags


def test_refuses_a_daily_series():
    daily = ReturnSeries(Frequency.DAILY, F0, (F0 + timedelta(days=1),), (0.01,))
    weekly = _weekly(0, [0.01])
    with pytest.raises(ValueError, match="weekly"):
        paired_weeks(daily, weekly, _window(0, 0))
    with pytest.raises(ValueError, match="weekly"):
        paired_weeks(weekly, daily, _window(0, 0))
