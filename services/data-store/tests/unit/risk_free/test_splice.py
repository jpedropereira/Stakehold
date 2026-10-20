from datetime import date
from decimal import Decimal

from data_store.risk_free.splice import (
    EONIA_SPREAD,
    ESTR_FIRST_DAY,
    splice,
    spliced_first_date,
)


def test_the_constants_are_the_ecbs_published_splice():
    assert ESTR_FIRST_DAY == date(2019, 10, 1)
    assert EONIA_SPREAD == Decimal("0.085")


def test_the_boundary_is_eonia_less_the_spread_then_estr():
    # €STR's first published value (-0.549) beside an illustrative last EONIA day.
    eonia = [(date(2019, 9, 30), Decimal("-0.4680"))]
    estr = [(date(2019, 10, 1), Decimal("-0.5490"))]

    points = splice(eonia, estr)

    assert points == [
        {"date": date(2019, 9, 30), "rate": Decimal("-0.5530"), "series": "eonia_adjusted"},
        {"date": date(2019, 10, 1), "rate": Decimal("-0.5490"), "series": "estr"},
    ]


def test_eonia_from_the_cutoff_on_is_ignored_even_where_estr_is_missing():
    """By then EONIA was €STR + 0.085 by definition; the date alone picks the series."""
    eonia = [(date(2019, 10, 1), Decimal("-0.4640")), (date(2019, 10, 2), Decimal("-0.4630"))]
    estr = [(date(2019, 10, 1), Decimal("-0.5490"))]

    points = splice(eonia, estr)

    assert points == [
        {"date": date(2019, 10, 1), "rate": Decimal("-0.5490"), "series": "estr"},
    ]


def test_estr_before_the_cutoff_is_ignored():
    estr = [(date(2019, 9, 30), Decimal("-0.5500")), (date(2019, 10, 1), Decimal("-0.5490"))]

    points = splice([], estr)

    assert [p["date"] for p in points] == [date(2019, 10, 1)]


def test_points_are_merged_in_date_order():
    eonia = [(date(2019, 9, 27), Decimal("-0.4670")), (date(2019, 9, 30), Decimal("-0.4680"))]
    estr = [(date(2019, 10, 2), Decimal("-0.5500")), (date(2019, 10, 1), Decimal("-0.5490"))]

    points = splice(eonia, estr)

    assert [p["date"] for p in points] == [
        date(2019, 9, 27),
        date(2019, 9, 30),
        date(2019, 10, 1),
        date(2019, 10, 2),
    ]


def test_a_positive_eonia_can_go_negative_after_the_spread_and_is_never_floored():
    eonia = [(date(2014, 6, 2), Decimal("0.0500"))]

    (point,) = splice(eonia, [])

    assert point["rate"] == Decimal("-0.0350")


def test_every_rate_comes_out_at_the_columns_scale():
    eonia = [(date(2008, 10, 1), Decimal("4.3"))]
    estr = [(date(2023, 10, 2), Decimal("3.9"))]

    points = splice(eonia, estr)

    assert [str(p["rate"]) for p in points] == ["4.2150", "3.9000"]


def test_no_rows_gives_no_points():
    assert splice([], []) == []


def test_first_date_is_eonias_when_it_starts_before_the_cutoff():
    assert spliced_first_date(date(1999, 1, 4), date(2019, 10, 1)) == date(1999, 1, 4)


def test_first_date_is_estrs_when_eonia_is_not_stored():
    assert spliced_first_date(None, date(2019, 10, 1)) == date(2019, 10, 1)


def test_first_date_ignores_eonia_that_only_starts_after_the_cutoff():
    assert spliced_first_date(date(2020, 1, 2), date(2019, 10, 1)) == date(2019, 10, 1)


def test_first_date_never_precedes_the_cutoff_on_estr_alone():
    assert spliced_first_date(None, date(2019, 9, 30)) == date(2019, 10, 1)


def test_first_date_is_none_when_eonia_only_starts_after_the_cutoff_and_estr_is_missing():
    assert spliced_first_date(date(2020, 1, 2), None) is None


def test_first_date_is_none_when_nothing_is_stored():
    assert spliced_first_date(None, None) is None
