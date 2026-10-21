"""Ex-post risk (spec §6.1, RK-11): the §5.1-5.5 figures on the portfolio's daily TWR of
invested positions, over the standard windows. Pure, like the rest of the library: the caller
(risk/flows.py) builds the series from twr/ and hands in plain values.

- **Windows.** 1Y, 3Y, 5Y, 10Y and Max, all ending at the series' last date, Max starting at its
  base date (§4.5). There's one series, so R10's common window is the series itself.
- **A window the history doesn't cover is greyed, not shortened.** Every figure in it carries
  "insufficient history (portfolio history from <base date>)" (R9), the row is kept, and the
  label still means what it says: a 5Y figure is never a 2-year one in disguise. The wording
  follows §5.6's "benchmark history too short (from <date>)".
- **Weekly measures run on `weekly.to_weekly` of the daily series.** Dates out of the market
  (RK-10 leaves them out) become NULL weeks there (R6), so they count against R8's budget at
  weekly frequency, as RK-10 agreed.
- **VaR's day mask is per date** (agreed for RK-11): a date counts when every ticker weighted at
  the previous date traded on it (`traded_days`, from twr/'s `TwrDay`). The holdings change over
  time, so one fixed set of assets, as `var_mask.var_days` takes for a mix, would drop days
  for tickers not even held then. The mask reaches `tail_figures` as a single set of days.
- **`nonsync_daily`** (§4.3, §5.5) is set unless the tickers held inside the window are exactly
  one EUR ticker: a non-EUR ticker mixes local and FX timings, and two tickers mix two closes.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date

from portfolio.risk.quality import WEEKLY_Q
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import (
    DrawdownFigures,
    ReturnFigures,
    TailFigures,
    WeeklyFigures,
    calmar_figure,
    drawdown_figures,
    return_figures,
    tail_figures,
    weekly_figures,
)
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure, Window
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import covers, standard_windows

_EUR = "EUR"


@dataclass(frozen=True)
class WindowFigures:
    window: Window
    covered: bool  # False: every figure is greyed, the history starts after the window does
    returns: ReturnFigures
    weekly: WeeklyFigures
    drawdown: DrawdownFigures
    calmar: RiskFigure
    tail: TailFigures


def ex_post_figures(
    daily: ReturnSeries,
    *,
    traded_days: AbstractSet[date],
    held: Mapping[date, AbstractSet[str]],
    currencies: Mapping[str, str],
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
) -> tuple[WindowFigures, ...]:
    """`daily` is the TWR as returns. `held` maps each of its dates to the tickers weighted at
    the previous date, and `currencies` each ticker to its FX currency. `rates` and
    `first_published` are None together when there's no risk-free series."""
    if not daily.dates:
        raise ValueError("ex-post figures need at least one return")
    weekly = to_weekly(daily)
    return tuple(
        (
            _measured(daily, weekly, window, traded_days, held, currencies, rates, first_published)
            if covers(daily, window)
            else _uncovered(window, daily.base_date)
        )
        for window in standard_windows(daily.last_date, daily.base_date)
    )


def _measured(daily, weekly, window, traded_days, held, currencies, rates, first_published):
    returns = return_figures(daily, window)
    drawdown = drawdown_figures(daily, window)
    tickers = set().union(*(held[d] for d in daily.dates if window.start < d <= window.end))
    nonsync = not (len(tickers) == 1 and currencies.get(next(iter(tickers))) == _EUR)
    return WindowFigures(
        window=window,
        covered=True,
        returns=returns,
        weekly=weekly_figures(weekly, window, rates, first_published),
        drawdown=drawdown,
        calmar=calmar_figure(returns.cagr, drawdown.max_drawdown),
        tail=tail_figures(daily, window, [traded_days], nonsync=nonsync),
    )


def _uncovered(window: Window, base_date: date) -> WindowFigures:
    reason = f"insufficient history (portfolio history from {base_date.isoformat()})"

    def greyed(frequency: Frequency) -> RiskFigure:
        return RiskFigure(
            value=None,
            window_start=window.start,
            window_end=window.end,
            frequency=frequency,
            # A daily q is empirical and undefined without observations (single_series._slice).
            q=WEEKLY_Q if frequency is Frequency.WEEKLY else 0.0,
            n_obs=0,
            n_gaps=0,
            flags=frozenset({Flag.INSUFFICIENT_HISTORY}),
            reason=reason,
        )

    daily, weekly = greyed(Frequency.DAILY), greyed(Frequency.WEEKLY)
    return WindowFigures(
        window=window,
        covered=False,
        returns=ReturnFigures(daily, daily),
        weekly=WeeklyFigures(weekly, weekly, weekly, weekly),
        drawdown=DrawdownFigures(daily, None, None, None, None, ()),
        calmar=daily,
        tail=TailFigures(daily, daily, daily, daily),
    )
