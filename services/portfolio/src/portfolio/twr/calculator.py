"""The whole portfolio's daily time-weighted return, invested positions only (RK-10; risk spec
stream 3, §6.1). Ex-post risk (RK-11) is measured on it.

Holdings-based, as agreed on 2026-10-20: r_t = sum_i w_(i,t-1) x r_eur,i,t.

- **Calendar.** The union of every return_snapshots row's date, across accounts and tickers.
- **Weights.** w_(i,t-1) is ticker i's share of the EUR market value at the previous calendar
  date. Each (account, ticker) row is forward-filled first (returns/performance.forward_filled)
  and only then summed per ticker, so one ticker in two accounts can't collapse into one
  account's row. A closed pair counts only through its last row. A sale dated on a day with no
  price leaves that row's quantity above 0, and forward-filling it would hold a stale weight for
  good.
- **Returns.** r_eur,i is eur_returns/calculator.py's daily EUR total return, with the dividend
  on its ex-date. Every return of ticker i dated in (t-1, t] is compounded into date t. That is
  usually one return, or none when i's exchange was shut on t, which adds 0 (R7's carry-forward)
  and leaves the move, FX included, to i's next return.
- **Flows don't move the return.** A buy or a sell only changes the next date's weights.
  Dividends are never booked from DIVIDEND transactions, which are dated on payment: the total
  return already has them on the ex-date, and the market value is price-only. That's the reason
  for this form rather than (MV_t + D_t - F_t) / MV_(t-1) - 1 (spec §6.1).
- **Gaps (R5).** A NULL return on a ticker held at t-1 makes t a gap: its return is None, never
  0, and growth stays flat across it. A NULL on a ticker not held at t-1 doesn't matter.
- **Nothing held.** When the eligible market value at t-1 is 0, there is no return on t and no
  point at all, so an exit isn't counted as a gap. The chain resumes at the next date with
  holdings at its previous date.
- **Exclusions.** A ticker with no entry in `returns` (no FX series, untracked, a failed fetch)
  is dropped, and the rest are renormalised to 100%. The caller names it.

What the holdings-based form gives up: dividends count gross of withholding tax, fees and the
gap between trade price and close are ignored, a position's first day counts from the next
close, and its last day runs close to close. Those are small next to the risk figures' own
sampling error. The cost-basis return on GET /returns/portfolio remains the figure for what was
actually earned.

No I/O. Decimal, like eur_returns/: the risk library converts to floats at its own boundary.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.eur_returns.calculator import EurReturns
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.returns.performance import Key, forward_filled

_EIGHT_PLACES = Decimal("0.00000001")
_ONE = Decimal("1")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class TwrPoint:
    price_date: date
    twr_return: Decimal | None  # None is a gap
    twr_growth: Decimal  # 1 at base_date; flat across a gap


@dataclass(frozen=True)
class TwrSeries:
    base_date: date | None  # the first date with holdings: growth's level 1; None when empty
    points: list[TwrPoint]
    n_gaps: int


class _Returns:
    """One ticker's EUR returns, read forward once: compounded over each (previous, current]."""

    def __init__(self, series: EurReturns):
        self._dates = series.dates
        self._values = series.r_eur
        self._index = 0

    def over(self, previous: date, current: date) -> Decimal | None:
        """Every return dated in (previous, current] compounded, 0 when there's none, and None
        when any of them is a gap. Calls must move forward in time."""
        while self._index < len(self._dates) and self._dates[self._index] <= previous:
            self._index += 1
        growth = _ONE
        while self._index < len(self._dates) and self._dates[self._index] <= current:
            value = self._values[self._index]
            self._index += 1
            if value is None:
                growth = None
            elif growth is not None:
                growth *= _ONE + value
        return None if growth is None else growth - _ONE


def compute_twr(
    snapshots: Sequence[ReturnSnapshot],
    returns: Mapping[str, EurReturns],
    *,
    closed: Collection[Key],
) -> TwrSeries:
    """The portfolio's daily TWR. `snapshots` are every account's return_snapshots rows,
    `returns` each eligible ticker's EUR returns, and `closed` the (account, ticker) pairs whose
    position is closed today."""
    last_row = {}
    for row in snapshots:
        key = (row.account_id, row.canonical_ticker)
        last_row[key] = max(last_row.get(key, row.price_date), row.price_date)

    readers = {ticker: _Returns(series) for ticker, series in returns.items()}
    base_date = None
    growth = _ONE
    n_gaps = 0
    points: list[TwrPoint] = []
    previous_date = None
    previous_values: dict[str, Decimal] = {}

    for current_date, rows in forward_filled(snapshots):
        if previous_date is not None:
            total = sum(previous_values.values(), _ZERO)
            if total != _ZERO:
                twr_return = _ZERO
                for ticker, value in previous_values.items():
                    ticker_return = readers[ticker].over(previous_date, current_date)
                    if ticker_return is None:
                        twr_return = None
                        break
                    twr_return += value / total * ticker_return
                if twr_return is None:
                    n_gaps += 1
                else:
                    growth *= _ONE + twr_return
                points.append(
                    TwrPoint(
                        current_date,
                        None if twr_return is None else _round(twr_return),
                        _round(growth),
                    )
                )

        values: dict[str, Decimal] = {}
        for key, row in rows.items():
            ticker = key[1]
            if ticker not in readers or (key in closed and current_date > last_row[key]):
                continue
            values[ticker] = values.get(ticker, _ZERO) + row.market_value_eur
        values = {ticker: value for ticker, value in values.items() if value != _ZERO}
        if base_date is None and values:
            base_date = current_date
        previous_date, previous_values = current_date, values

    return TwrSeries(base_date=base_date, points=points, n_gaps=n_gaps)


def _round(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
