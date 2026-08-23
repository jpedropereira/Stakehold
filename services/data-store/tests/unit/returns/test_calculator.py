"""Unit tests for the returns arithmetic itself -- the highest-risk part of Phase 2.

See docs/schema.md ("What `close` actually contains") for
why there is no split term in these formulas: `raw_market_data.close` is already retroactively
split-adjusted by the provider, so applying a further split correction double-adjusts.
"""

import logging
from decimal import Decimal
from math import log

import pandas as pd
import pytest
from data_store.returns.calculator import compute_returns


def make_prices(rows):
    return pd.DataFrame(
        rows, columns=["ticker", "price_date", "close", "dividends", "stock_splits"]
    )


def by_date(df):
    return {row.price_date: row for row in df.itertuples()}


def test_computes_price_dividend_simple_and_log_returns_over_a_plain_series():
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", 102.0, 0.0, 0.0],
            ["AAPL", "2026-01-03", 101.0, 0.5, 0.0],
        ]
    )

    result = by_date(compute_returns(prices))

    day2 = result["2026-01-02"]
    assert day2.price_return == pytest.approx(102.0 / 100.0 - 1)
    assert day2.dividend_return == pytest.approx(0.0)
    assert day2.simple_return == pytest.approx(102.0 / 100.0 - 1)
    assert day2.log_return == pytest.approx(log(102.0 / 100.0))

    day3 = result["2026-01-03"]
    assert day3.price_return == pytest.approx(101.0 / 102.0 - 1)
    assert day3.dividend_return == pytest.approx(0.5 / 102.0)
    assert day3.simple_return == pytest.approx((101.0 + 0.5) / 102.0 - 1)
    assert day3.log_return == pytest.approx(log((101.0 + 0.5) / 102.0))


def test_split_day_produces_the_plain_close_over_close_return_with_no_split_adjustment():
    # Real AAPL numbers around the 2020-08-31 4:1 split. An earlier draft of this design divided
    # the prior close by the split ratio, which produced +313.56% here instead of the real
    # +3.39% -- this is the regression test for that bug.
    prices = make_prices(
        [
            ["AAPL", "2020-08-28", 124.8075, 0.0, 0.0],
            ["AAPL", "2020-08-31", 129.04, 0.0, 4.0],
        ]
    )

    result = by_date(compute_returns(prices))

    split_day = result["2020-08-31"]
    assert split_day.price_return == pytest.approx(129.04 / 124.8075 - 1, abs=1e-4)
    assert split_day.price_return < 0.05  # sanity bound against a reintroduced split term


def test_first_date_of_a_ticker_has_populated_prices_but_null_returns():
    prices = make_prices([["AAPL", "2026-01-01", 100.0, 0.0, 0.0]])

    row = compute_returns(prices).iloc[0]

    assert row["split_adjusted_close"] == 100.0
    assert pd.isna(row["price_return"])
    assert pd.isna(row["dividend_return"])
    assert pd.isna(row["simple_return"])
    assert pd.isna(row["log_return"])


def test_simple_return_equals_price_return_plus_dividend_return():
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", 103.0, 0.4, 0.0],
        ]
    )

    row = compute_returns(prices).iloc[1]

    assert row["simple_return"] == pytest.approx(row["price_return"] + row["dividend_return"])


def test_log_return_equals_log_of_one_plus_simple_return():
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", 97.0, 0.4, 0.0],
        ]
    )

    row = compute_returns(prices).iloc[1]

    assert row["log_return"] == pytest.approx(log(1 + row["simple_return"]))


def test_future_split_factor_is_one_everywhere_for_a_split_free_ticker():
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", 101.0, 0.0, 0.0],
            ["AAPL", "2026-01-03", 102.0, 0.0, 0.0],
        ]
    )

    result = compute_returns(prices)

    assert (result["future_split_factor"] == 1.0).all()


def test_future_split_factor_applies_only_to_dates_strictly_before_a_mid_series_split():
    # A 4:1 split on d4 of a 5-day series should yield [4, 4, 4, 1, 1] -- worked through in the
    # design doc. The split's own day is not adjusted, since its stored close already reflects
    # the post-split scale.
    dates = [f"2026-01-0{i}" for i in range(1, 6)]
    prices = make_prices(
        [
            ["AAPL", dates[0], 400.0, 0.0, 0.0],
            ["AAPL", dates[1], 404.0, 0.0, 0.0],
            ["AAPL", dates[2], 408.0, 0.0, 0.0],
            ["AAPL", dates[3], 103.0, 0.0, 4.0],
            ["AAPL", dates[4], 104.0, 0.0, 0.0],
        ]
    )

    result = compute_returns(prices).set_index("price_date")

    assert list(result.loc[dates, "future_split_factor"]) == [4.0, 4.0, 4.0, 1.0, 1.0]


def test_future_split_factor_compounds_across_two_splits():
    dates = [f"2026-01-0{i}" for i in range(1, 5)]
    prices = make_prices(
        [
            ["AAPL", dates[0], 3200.0, 0.0, 0.0],
            ["AAPL", dates[1], 400.0, 0.0, 8.0],  # 8:1 split
            ["AAPL", dates[2], 100.0, 0.0, 4.0],  # 4:1 split
            ["AAPL", dates[3], 101.0, 0.0, 0.0],
        ]
    )

    result = compute_returns(prices).set_index("price_date")

    assert list(result.loc[dates, "future_split_factor"]) == [32.0, 4.0, 1.0, 1.0]


def test_future_split_factor_handles_a_reverse_split():
    # 193 of the 791 split-affected tickers in the real data are reverse splits (ratio < 1),
    # not hypothetical.
    dates = [f"2026-01-0{i}" for i in range(1, 4)]
    prices = make_prices(
        [
            ["XYZ", dates[0], 1.0, 0.0, 0.0],
            ["XYZ", dates[1], 4.0, 0.0, 0.25],  # 1-for-4 reverse split
            ["XYZ", dates[2], 4.1, 0.0, 0.0],
        ]
    )

    result = compute_returns(prices).set_index("price_date")

    assert list(result.loc[dates, "future_split_factor"]) == [0.25, 1.0, 1.0]


def test_two_interleaved_tickers_compute_independently():
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["MSFT", "2026-01-01", 200.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", 110.0, 0.0, 0.0],
            ["MSFT", "2026-01-02", 190.0, 0.0, 0.0],
        ]
    )

    result = compute_returns(prices)

    aapl_return = result[(result["ticker"] == "AAPL") & (result["price_date"] == "2026-01-02")][
        "price_return"
    ].iloc[0]
    msft_return = result[(result["ticker"] == "MSFT") & (result["price_date"] == "2026-01-02")][
        "price_return"
    ].iloc[0]
    assert aapl_return == pytest.approx(0.10)
    assert msft_return == pytest.approx(-0.05)


def test_row_order_in_the_input_does_not_affect_the_result():
    ordered = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", 110.0, 0.0, 0.0],
        ]
    )
    shuffled = ordered.iloc[::-1].reset_index(drop=True)

    assert compute_returns(ordered).equals(compute_returns(shuffled))


def test_non_positive_close_is_dropped_and_logged_and_the_next_return_is_null(caplog):
    caplog.set_level(logging.WARNING)
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", 0.0, 0.0, 0.0],  # garbage/missing close
            ["AAPL", "2026-01-03", 105.0, 0.0, 0.0],
        ]
    )

    result = compute_returns(prices)

    assert list(result["price_date"]) == ["2026-01-01", "2026-01-03"]
    day3 = result[result["price_date"] == "2026-01-03"].iloc[0]
    assert pd.isna(day3["price_return"])
    assert "1" in caplog.text  # count of dropped rows


def test_nan_close_is_dropped_and_logged(caplog):
    caplog.set_level(logging.WARNING)
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", 100.0, 0.0, 0.0],
            ["AAPL", "2026-01-02", float("nan"), 0.0, 0.0],
        ]
    )

    result = compute_returns(prices)

    assert list(result["price_date"]) == ["2026-01-01"]
    assert "dropping" in caplog.text


def test_decimal_valued_columns_are_handled_without_error_or_object_dtype():
    # psycopg 3 returns decimal.Decimal for NUMERIC columns, which makes a pandas column
    # object-dtype -- shift/cumprod/log then silently degrade or fail. Guards that trap.
    prices = make_prices(
        [
            ["AAPL", "2026-01-01", Decimal("100.0"), Decimal("0.0"), Decimal("0.0")],
            ["AAPL", "2026-01-02", Decimal("102.0"), Decimal("0.5"), Decimal("0.0")],
        ]
    )

    result = compute_returns(prices)

    row = result.iloc[1]
    assert isinstance(row["price_return"], float)
    assert row["price_return"] == pytest.approx(102.0 / 100.0 - 1)
