"""Pure return arithmetic -- no I/O, no database, no provider. See docs/schema.md ("What
`close` actually contains") for why there is no split term here: `raw_market_data.close` is
already retroactively split-adjusted by the provider, so a further split correction would
double-adjust.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_OUTPUT_COLUMNS = [
    "ticker",
    "price_date",
    "split_adjusted_close",
    "future_split_factor",
    "price_return",
    "dividend_return",
    "simple_return",
    "log_return",
]


_IMPLAUSIBLE_RETURN_THRESHOLD = 1.0  # >100% in a day. Root-caused against the real 8.95M-row
# dataset: 460 rows crossed this table-wide, 93% traced to a close computed across a day with
# raw_market_data.volume = 0 (mostly exchange holidays, where the provider's close is a garbage
# value at a roughly-constant-but-wrong per-ticker scale), 3% to a `dividends` value implying a
# >100% one-day yield -- see docs/schema.md. dividends are never negative (NOT NULL DEFAULT 0),
# so dividend_return >= 0 always and simple_return >= price_return always: checking
# simple_return alone can't miss a price-driven blowup, and the dividend_return check below only
# disambiguates cause, it isn't an independent detector.


def compute_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """prices: ticker, price_date, close, dividends, stock_splits, volume (any row order).

    One output row per input row that has a usable close, ordered by (ticker, price_date).
    `split_adjusted_close` / `future_split_factor` are always populated; the four return columns
    are null on each ticker's first stored date, since no prior close exists to compute a return
    from, and also null on a row flagged as an implausible single-day return (see
    `_IMPLAUSIBLE_RETURN_THRESHOLD`) -- the price itself is left untouched in that case, only the
    derived returns are suppressed.
    """
    df = prices.sort_values(["ticker", "price_date"]).reset_index(drop=True)

    # psycopg 3 returns decimal.Decimal for NUMERIC columns, which makes the pandas column
    # object-dtype -- shift/cumprod/log then silently degrade to per-element Python or fail
    # outright. Cast explicitly rather than relying on pandas to infer it.
    for column in ("close", "dividends", "stock_splits", "volume"):
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

    prior_close = df.groupby("ticker")["_working_close"].shift(1)
    working_close = df["_working_close"]
    df["price_return"] = working_close / prior_close - 1
    df["dividend_return"] = df["dividends"] / prior_close
    df["simple_return"] = (working_close + df["dividends"]) / prior_close - 1
    df["log_return"] = np.log((working_close + df["dividends"]) / prior_close)

    # NULL volume is not treated as zero-volume: raw_market_data.volume has no NOT NULL and is
    # legitimately missing sometimes, distinct from a confirmed zero-trade day. Comparisons
    # against NaN evaluate to False, so this falls out for free.
    prior_volume = df.groupby("ticker")["volume"].shift(1)
    zero_volume_adjacent = (df["volume"] == 0) | (prior_volume == 0)
    dividend_glitch = df["dividend_return"].abs() > _IMPLAUSIBLE_RETURN_THRESHOLD
    implausible = (df["simple_return"].abs() > _IMPLAUSIBLE_RETURN_THRESHOLD) & (
        zero_volume_adjacent | dividend_glitch
    )
    if implausible.any():
        logger.warning(
            "compute_returns: nulling return columns for %d row(s) flagged as an implausible "
            ">100%% single-day move (zero-volume-adjacent close or dividend-field glitch).",
            int(implausible.sum()),
        )
    for column in ("price_return", "dividend_return", "simple_return", "log_return"):
        df[column] = df[column].mask(implausible)

    return df.loc[~bad_close, _OUTPUT_COLUMNS].reset_index(drop=True)
