"""risk/regimes.py (RK-28): clips each regime to the series it measures, and measures the
series and the benchmark over the same clipped window.

Calendar: 2020-01-03 is a Friday. Fixtures are consecutive weekdays after their base date.
"""

from datetime import date, timedelta

import pytest
from portfolio.risk.regimes import RegimePeriod, regime_rows
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window

FRIDAY = date(2020, 1, 3)


def _weekdays(base, n):
    days, day = [], base
    while len(days) < n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return tuple(days)


def _daily(values, base=FRIDAY):
    return ReturnSeries(Frequency.DAILY, base, _weekdays(base, len(values)), tuple(values))


SERIES = _daily([0.01, -0.02, 0.03, -0.04, 0.05, -0.01, 0.02, 0.01, -0.03, 0.02])
# Starts four weekdays later than SERIES, on its 4th date.
BENCHMARK = _daily([0.02, -0.01, 0.01, 0.02, -0.02, 0.01], base=SERIES.dates[3])


def test_a_regime_inside_the_series_is_measured_on_its_own_dates():
    period = RegimePeriod("crash", SERIES.dates[1], SERIES.dates[4])

    (row,) = regime_rows(SERIES, [period], BENCHMARK)

    assert row.regime_id == "crash"
    assert row.measured == Window("crash", SERIES.dates[1], SERIES.dates[4])
    assert (row.partial, row.ongoing) == (False, False)
    # The returns dated after the start: +3%, -4%, +5%.
    assert row.portfolio.cumulative_return.value == pytest.approx(1.03 * 0.96 * 1.05 - 1)
    assert row.portfolio.max_drawdown.value == pytest.approx(-0.04)


def test_a_regime_starting_before_the_series_is_clipped_to_its_base_and_partial():
    period = RegimePeriod("long", date(2019, 6, 1), SERIES.dates[2])

    (row,) = regime_rows(SERIES, [period], BENCHMARK)

    assert row.measured == Window("long", SERIES.base_date, SERIES.dates[2])
    assert row.partial is True
    assert row.portfolio.cumulative_return.value == pytest.approx(1.01 * 0.98 * 1.03 - 1)


def test_a_regime_ending_after_the_series_is_clipped_to_its_last_date_and_partial():
    period = RegimePeriod("late", SERIES.dates[7], SERIES.last_date + timedelta(days=30))

    (row,) = regime_rows(SERIES, [period], BENCHMARK)

    assert row.measured.end == SERIES.last_date
    assert (row.partial, row.ongoing) == (True, False)


def test_an_ongoing_regime_is_measured_to_the_last_date_and_not_partial():
    period = RegimePeriod("war", SERIES.dates[7], None)

    (row,) = regime_rows(SERIES, [period], BENCHMARK)

    assert row.measured == Window("war", SERIES.dates[7], SERIES.last_date)
    assert (row.partial, row.ongoing) == (False, True)
    assert row.portfolio.cumulative_return.value == pytest.approx(0.97 * 1.02 - 1)


def test_an_ongoing_regime_starting_before_the_series_is_both_partial_and_ongoing():
    (row,) = regime_rows(SERIES, [RegimePeriod("era", date(2019, 1, 1), None)], BENCHMARK)

    assert (row.partial, row.ongoing) == (True, True)
    assert row.measured == Window("era", SERIES.base_date, SERIES.last_date)


def test_a_regime_that_only_touches_the_series_base_date_is_dropped():
    # data-store's overlap is inclusive, so a regime ending on the base date is served, but no
    # return is dated inside it.
    touching = RegimePeriod("before", date(2019, 6, 1), SERIES.base_date)
    inside = RegimePeriod("inside", SERIES.dates[0], SERIES.dates[2])

    rows = regime_rows(SERIES, [touching, inside], BENCHMARK)

    assert [row.regime_id for row in rows] == ["inside"]


def test_a_regime_entirely_outside_the_series_is_dropped():
    outside = RegimePeriod("old", date(2001, 1, 1), date(2002, 1, 1))

    assert regime_rows(SERIES, [outside], BENCHMARK) == ()


def test_the_benchmark_is_measured_on_the_same_clipped_window():
    period = RegimePeriod("tail", SERIES.dates[5], date(2030, 1, 1))

    (row,) = regime_rows(SERIES, [period], BENCHMARK)

    assert row.benchmark.cumulative_return.window_start == SERIES.dates[5]
    assert row.benchmark.cumulative_return.window_end == SERIES.last_date
    # BENCHMARK's returns dated after SERIES.dates[5] = its own 3rd date: +1%, +2%, -2%, +1%.
    assert row.benchmark.cumulative_return.value == pytest.approx(1.01 * 1.02 * 0.98 * 1.01 - 1)
    assert row.benchmark.max_drawdown.value == pytest.approx(-0.02)


def test_a_benchmark_starting_after_the_regime_is_greyed_with_its_first_date():
    period = RegimePeriod("early", SERIES.dates[0], SERIES.dates[5])

    (row,) = regime_rows(SERIES, [period], BENCHMARK)

    assert row.portfolio.cumulative_return.value is not None
    for figure in (row.benchmark.cumulative_return, row.benchmark.max_drawdown):
        assert figure.value is None
        assert figure.reason == f"benchmark history too short (from {BENCHMARK.base_date})"
        assert figure.window_start == SERIES.dates[0]
        assert figure.window_end == SERIES.dates[5]


def test_a_benchmark_ending_before_the_regime_is_greyed():
    short = _daily([0.01, 0.01], base=SERIES.dates[0])
    period = RegimePeriod("later", SERIES.dates[0], SERIES.dates[5])

    (row,) = regime_rows(SERIES, [period], short)

    assert row.benchmark.cumulative_return.value is None
    assert row.benchmark.cumulative_return.reason == (
        f"benchmark history too short (to {short.last_date})"
    )


def test_an_unavailable_benchmark_greys_both_of_its_figures_with_the_reason():
    period = RegimePeriod("crash", SERIES.dates[1], SERIES.dates[4])

    (row,) = regime_rows(SERIES, [period], "benchmark unavailable: not tracked")

    assert row.portfolio.max_drawdown.value is not None
    for figure in (row.benchmark.cumulative_return, row.benchmark.max_drawdown):
        assert figure.value is None
        assert figure.reason == "benchmark unavailable: not tracked"


def test_rows_keep_the_order_of_the_periods():
    periods = [
        RegimePeriod("b", SERIES.dates[2], SERIES.dates[4]),
        RegimePeriod("a", SERIES.dates[0], SERIES.dates[1]),
    ]

    assert [row.regime_id for row in regime_rows(SERIES, periods, BENCHMARK)] == ["b", "a"]


def test_a_regime_ending_before_it_starts_is_refused():
    with pytest.raises(ValueError):
        RegimePeriod("bad", date(2020, 2, 1), date(2020, 1, 1))


def test_short_regime_flag_reaches_the_row():
    period = RegimePeriod("crash", SERIES.dates[1], SERIES.dates[4])

    (row,) = regime_rows(SERIES, [period], BENCHMARK)

    assert Flag.SHORT_REGIME in row.portfolio.max_drawdown.flags
