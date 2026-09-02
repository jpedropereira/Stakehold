import logging

import pandas as pd
import pytest
from data_store.macro.calculator import (
    SOURCE_REPORTED,
    YOY_PCT_CHANGE,
    compute_indicator_rates,
)


def make_raw(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "source",
            "indicator_code",
            "region",
            "observation_date",
            "indicator_type",
            "frequency",
            "value",
            "unit",
        ],
    )


def test_eurostat_rows_pass_through_as_source_reported():
    raw = make_raw(
        [
            ["eurostat", "prc_hicp_manr", "EA", "2024-01-01", "inflation", "monthly", 2.8, "u"],
            ["eurostat", "prc_hicp_manr", "EA", "2024-02-01", "inflation", "monthly", 2.6, "u"],
        ]
    )

    result = compute_indicator_rates(raw)

    assert list(result["rate_value"]) == [2.8, 2.6]
    assert (result["calculation_method"] == SOURCE_REPORTED).all()


def test_fred_monthly_series_computes_yoy_pct_change_with_a_twelve_row_lag():
    rows = [
        ["fred", "CPIAUCSL", "US", f"2023-{m:02d}-01", "inflation", "monthly", 100.0 + m, "idx"]
        for m in range(1, 13)
    ] + [["fred", "CPIAUCSL", "US", "2024-01-01", "inflation", "monthly", 121.0, "idx"]]
    raw = make_raw(rows)

    result = compute_indicator_rates(raw)

    # Only the 13th observation (2024-01) has a prior-year value (2023-01 = 101.0) to compare
    # against -- the first twelve rows of the series are dropped, not nulled.
    assert len(result) == 1
    row = result.iloc[0]
    assert row["observation_date"] == "2024-01-01"
    assert row["rate_value"] == pytest.approx((121.0 / 101.0 - 1) * 100)
    assert row["calculation_method"] == YOY_PCT_CHANGE


def test_fred_quarterly_series_computes_yoy_pct_change_with_a_four_row_lag():
    rows = [
        ["fred", "GDPC1", "US", "2023-01-01", "growth", "quarterly", 20000.0, "lvl"],
        ["fred", "GDPC1", "US", "2023-04-01", "growth", "quarterly", 20100.0, "lvl"],
        ["fred", "GDPC1", "US", "2023-07-01", "growth", "quarterly", 20200.0, "lvl"],
        ["fred", "GDPC1", "US", "2023-10-01", "growth", "quarterly", 20300.0, "lvl"],
        ["fred", "GDPC1", "US", "2024-01-01", "growth", "quarterly", 20600.0, "lvl"],
    ]
    raw = make_raw(rows)

    result = compute_indicator_rates(raw)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["observation_date"] == "2024-01-01"
    assert row["rate_value"] == pytest.approx((20600.0 / 20000.0 - 1) * 100)


def test_groups_are_computed_independently_by_source_region_and_indicator_type():
    raw = make_raw(
        [
            ["eurostat", "prc_hicp_manr", "EA", "2024-01-01", "inflation", "monthly", 2.8, "u"],
            ["eurostat", "prc_hicp_manr", "DE", "2024-01-01", "inflation", "monthly", 3.1, "u"],
            ["eurostat", "namq_10_gdp", "EA", "2024-01-01", "growth", "quarterly", 0.7, "u"],
        ]
    )

    result = compute_indicator_rates(raw)

    assert len(result) == 3
    ea_inflation = result[(result["region"] == "EA") & (result["indicator_type"] == "inflation")]
    de_inflation = result[(result["region"] == "DE") & (result["indicator_type"] == "inflation")]
    assert ea_inflation["rate_value"].iloc[0] == 2.8
    assert de_inflation["rate_value"].iloc[0] == 3.1


def test_two_indicator_codes_sharing_a_type_are_skipped_and_logged(caplog):
    # macro_indicator_rates has no indicator_code column -- (source, region, indicator_type,
    # observation_date) is the whole key -- so two distinct series here would silently blend
    # into one YoY computation and collide on upsert. Must be refused, not computed wrong.
    caplog.set_level(logging.WARNING)
    raw = make_raw(
        [
            ["fred", "CPIAUCSL", "US", "2024-01-01", "inflation", "monthly", 300.1, "u"],
            ["fred", "CPIALTT01", "US", "2024-01-01", "inflation", "monthly", 5.0, "u"],
        ]
    )

    result = compute_indicator_rates(raw)

    assert result.empty
    assert "more than one indicator_code" in caplog.text
    assert "CPIAUCSL" in caplog.text and "CPIALTT01" in caplog.text


def test_unrecognized_frequency_is_skipped_and_logged(caplog):
    caplog.set_level(logging.WARNING)
    raw = make_raw([["fred", "XYZ", "US", "2024-01-01", "inflation", "weekly", 1.0, "u"]])

    result = compute_indicator_rates(raw)

    assert result.empty
    assert "weekly" in caplog.text


def test_row_order_in_the_input_does_not_affect_the_result():
    ordered = make_raw(
        [
            ["eurostat", "prc_hicp_manr", "EA", "2024-01-01", "inflation", "monthly", 2.8, "u"],
            ["eurostat", "prc_hicp_manr", "EA", "2024-02-01", "inflation", "monthly", 2.6, "u"],
        ]
    )
    shuffled = ordered.iloc[::-1].reset_index(drop=True)

    assert compute_indicator_rates(ordered).equals(compute_indicator_rates(shuffled))


def test_empty_input_returns_empty_output_with_expected_columns():
    raw = make_raw([])

    result = compute_indicator_rates(raw)

    assert result.empty
    assert list(result.columns) == [
        "source",
        "region",
        "indicator_type",
        "observation_date",
        "frequency",
        "rate_value",
        "calculation_method",
    ]


def test_decimal_valued_columns_are_handled_without_error():
    from decimal import Decimal

    raw = make_raw(
        [
            [
                "eurostat",
                "prc_hicp_manr",
                "EA",
                "2024-01-01",
                "inflation",
                "monthly",
                Decimal("2.8"),
                "u",
            ]
        ]
    )

    result = compute_indicator_rates(raw)

    assert isinstance(result.iloc[0]["rate_value"], float)
    assert result.iloc[0]["rate_value"] == pytest.approx(2.8)
