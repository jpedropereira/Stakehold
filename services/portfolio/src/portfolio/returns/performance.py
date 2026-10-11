"""Pure computation of an account's time-weighted growth since inception, one figure per priced
day, from its return_snapshots rows and the money its BUY/SELL transactions moved.

Time-weighted means money going in or out never moves the figure by itself -- only what the
holdings did with it does. Each day's return is

    r_d = (MV_d + Div_d + Out_d - MV_{d-1} - In_d) / (MV_{d-1} + In_d)

where MV is the summed market value, Div the dividends that arrived that day, In the money spent
on buys (fees included) and Out the proceeds of sells. Growth on day d is the product of
(1 + r) up to d, minus 1. Buys count at the start of the day and sells at its end: the day a
position sells out has a zero market value, and treating its proceeds as start-of-day money
would leave nothing (or less than nothing) to divide by. A day with nothing invested at its
start and nothing bought holds growth flat.

Calculated on read, never stored -- see docs/architecture.md's "Growth since inception is
computed on read". No I/O, no
Postgres: pipeline.py resolves each BUY/SELL to its ticker and hands the flows in as plain data,
the same split returns/calculator.py uses.
"""

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.returns.calculator import ReturnSnapshot

# The precision return_snapshots' own pct columns use (NUMERIC(18,8)), applied only at emission
# so compounding runs at full Decimal precision.
_EIGHT_PLACES = Decimal("0.00000001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class TradeFlow:
    """The money one BUY or SELL moved, resolved to the ticker it traded."""

    canonical_ticker: str
    date: date  # trade_datetime's UTC date, the same date dividends are bucketed by
    eur_amount: (
        Decimal  # transactions.eur_amount as stored: negative for a buy, positive for a sell
    )


@dataclass(frozen=True)
class GrowthPoint:
    price_date: date
    growth_pct: Decimal  # a fraction: 0.1240 is +12.40%


def compute_growth(
    snapshots: Sequence[ReturnSnapshot], flows: Sequence[TradeFlow]
) -> list[GrowthPoint]:
    """One point per date any ticker has a row for, oldest first.

    A ticker with no row on a date holds its last row's value, the same forward-fill web's
    chart uses; before its first row it contributes nothing. A flow lands on its own ticker's
    first row dated on or after the trade -- the first day that ticker's market value reflects
    it, so a weekend trade or one on a day only another ticker was priced still balances. A flow
    with no such row (its ticker has none, or the trade is newer than its last) is left out: no
    market value reflects it yet, so counting it would read as a loss.
    """
    by_ticker: dict[str, list[ReturnSnapshot]] = {}
    for row in snapshots:
        by_ticker.setdefault(row.canonical_ticker, []).append(row)
    series = {
        ticker: sorted(rows, key=lambda r: r.price_date) for ticker, rows in by_ticker.items()
    }
    row_dates = {ticker: [r.price_date for r in rows] for ticker, rows in series.items()}

    money_in: dict[date, Decimal] = {}
    money_out: dict[date, Decimal] = {}
    for flow in flows:
        dates = row_dates.get(flow.canonical_ticker)
        if not dates:
            continue
        idx = bisect_left(dates, flow.date)
        if idx == len(dates):
            continue
        lands_on = dates[idx]
        if flow.eur_amount < _ZERO:
            money_in[lands_on] = money_in.get(lands_on, _ZERO) - flow.eur_amount
        else:
            money_out[lands_on] = money_out.get(lands_on, _ZERO) + flow.eur_amount

    pointers = {ticker: -1 for ticker in series}
    previous_value = _ZERO
    previous_dividends = _ZERO
    growth_factor = _ONE
    points: list[GrowthPoint] = []

    for current_date in sorted({row.price_date for row in snapshots}):
        value = _ZERO
        dividends = _ZERO
        for ticker, rows in series.items():
            idx = pointers[ticker]
            while idx + 1 < len(rows) and rows[idx + 1].price_date <= current_date:
                idx += 1
            pointers[ticker] = idx
            if idx == -1:
                continue  # not held yet as of this date -- contributes nothing
            value += rows[idx].market_value_eur
            dividends += rows[idx].dividends_received_eur

        invested = previous_value + money_in.get(current_date, _ZERO)
        if invested > _ZERO:
            gain = (
                value
                + (dividends - previous_dividends)
                + money_out.get(current_date, _ZERO)
                - invested
            )
            growth_factor *= _ONE + gain / invested

        points.append(
            GrowthPoint(
                price_date=current_date,
                growth_pct=(growth_factor - _ONE).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP),
            )
        )
        previous_value = value
        previous_dividends = dividends

    return points
