"""risk/types.py: a return series refuses to be built in a shape the rest of the library would
misread, and turns into a numpy array with NaN, never 0, where data-store had a NULL."""

import math
from datetime import date

import pytest
from portfolio.risk.types import Frequency, ReturnSeries, Window, to_array

BASE = date(2026, 1, 2)  # a Friday


def test_a_well_formed_series_keeps_its_fields():
    series = ReturnSeries(Frequency.DAILY, BASE, (date(2026, 1, 5), date(2026, 1, 6)), (0.01, None))

    assert series.base_date == BASE
    assert series.last_date == date(2026, 1, 6)


def test_an_empty_series_ends_on_its_base_date():
    series = ReturnSeries(Frequency.WEEKLY, BASE, (), ())

    assert series.last_date == BASE


def test_dates_and_values_must_be_the_same_length():
    with pytest.raises(ValueError):
        ReturnSeries(Frequency.DAILY, BASE, (date(2026, 1, 5),), (0.01, 0.02))


@pytest.mark.parametrize(
    "dates",
    [
        (date(2026, 1, 6), date(2026, 1, 5)),  # out of order
        (date(2026, 1, 5), date(2026, 1, 5)),  # repeated
    ],
)
def test_dates_must_strictly_increase(dates):
    with pytest.raises(ValueError):
        ReturnSeries(Frequency.DAILY, BASE, dates, (0.01, 0.02))


def test_every_date_must_be_after_the_base_date():
    # The base is G0's date: a return dated on it would have no prior close.
    with pytest.raises(ValueError):
        ReturnSeries(Frequency.DAILY, BASE, (BASE,), (0.01,))


def test_to_array_turns_a_null_into_nan_not_zero():
    series = ReturnSeries(Frequency.DAILY, BASE, (date(2026, 1, 5), date(2026, 1, 6)), (0.01, None))

    values = to_array(series)

    assert values[0] == 0.01
    assert math.isnan(values[1])


def test_a_window_cannot_end_before_it_starts():
    with pytest.raises(ValueError):
        Window("1Y", date(2026, 1, 2), date(2026, 1, 1))
