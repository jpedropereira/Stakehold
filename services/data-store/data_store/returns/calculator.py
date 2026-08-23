"""Pure return arithmetic -- no I/O, no database, no provider. See
dev/PLAN_RETURNS_CALCULATION.md and docs/schema.md ("What `close` actually contains") for why
there is no split term here: `raw_market_data.close` is already retroactively split-adjusted by
the provider, so a further split correction would double-adjust.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_OUTPUT_COLUMNS = [
    "ticker",
    "price_date",
    "split_adjusted_close",
    "as_traded_close",
    "future_split_factor",
    "price_return",
    "dividend_return",
    "simple_return",
    "log_return",
]


def compute_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """prices: ticker, price_date, close, dividends, stock_splits (any row order).

    One output row per input row that has a usable close, ordered by (ticker, price_date).
    `split_adjusted_close` / `future_split_factor` / `as_traded_close` are always populated; the
    four return columns are null on each ticker's first stored date, since no prior close exists
    to compute a return from.
    """
    df = prices.sort_values(["ticker", "price_date"]).reset_index(drop=True)

    # psycopg 3 returns decimal.Decimal for NUMERIC columns, which makes the pandas column
    # object-dtype -- shift/cumprod/log then silently degrade to per-element Python or fail
    # outright. Cast explicitly rather than relying on pandas to infer it.
    for column in ("close", "dividends", "stock_splits"):
        df[column] = df[column].astype("float64")

    bad_close = df["close"].isna() | (df["close"] <= 0)
    if bad_close.any():
        logger.warning(
            "compute_returns: dropping %d row(s) with a non-positive or missing close -- "
            "cannot anchor a return to it.",
            int(bad_close.sum()),
        )
    # Masked (not dropped yet): a NaN here propagates through shift(1), so the return
    # immediately after a bad-close day comes out null instead of being computed against a
    # bogus, two-days-back prior close.
    df["_working_close"] = df["close"].mask(bad_close)

    split_ratio = df["stock_splits"].where(df["stock_splits"] != 0, 1.0)
    df["future_split_factor"] = split_ratio.groupby(df["ticker"]).transform(
        lambda g: g[::-1].shift(1, fill_value=1.0).cumprod()[::-1]
    )

    df["split_adjusted_close"] = df["close"]
    df["as_traded_close"] = df["close"] * df["future_split_factor"]

    prior_close = df.groupby("ticker")["_working_close"].shift(1)
    working_close = df["_working_close"]
    df["price_return"] = working_close / prior_close - 1
    df["dividend_return"] = df["dividends"] / prior_close
    df["simple_return"] = (working_close + df["dividends"]) / prior_close - 1
    df["log_return"] = np.log((working_close + df["dividends"]) / prior_close)

    return df.loc[~bad_close, _OUTPUT_COLUMNS].reset_index(drop=True)
