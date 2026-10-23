"""Ex-ante risk of today's mix over one requested window (RK-17, spec §6.2-6.3, §7.1): the
decomposition, the constant-weight figures and the correlation matrix together, each on the
window the spec gives it. Pure, like the rest of the library: risk/flows.py fetches the
holdings, converts them to daily EUR returns and normalises the weights.

- **The requested window** is one of the standard ones (§4.5), ending on the holdings' latest
  common date. Max starts at the *oldest* holding's base, not the common start, so a younger
  holding shortens it like any other window and `limited_by` names it: the UI can offer to
  exclude that holding (R12).
- **The decomposition runs on the listwise weekly window** (R12, `ex_ante.decomposition_figures`),
  which a late holding shortens. Its start is the **effective** window's.
- **The constant-weight figures run over the effective window**: from the listwise start to the
  common end. The daily figures take it as given; the weekly ones shrink it to the same whole
  weeks as the panel, and the mix's weekly series is `constant_weight_series` of the weekly
  returns (not `to_weekly` of the daily mix), so its NULL weeks are the panel's dropped weeks and
  its weekly volatility is σ_p exactly (§6.2's check). The page never shows two volatilities for
  one mix. All 12 ex-post figures are measured (agreed for RK-17), through
  `ex_post.series_figures`.
- **VaR's day mask** is the days every holding has a return row (R7, `var_mask.var_days`): on the
  constant-weight daily union calendar a holding with no row is a closed exchange at 0%.
  `nonsync_daily` is set unless the mix is one EUR holding, as in ex-post.
- **The correlation matrix keeps the requested window** (R13): it is for display, and each
  pairwise cell covers the weeks that pair shares, so a late holding doesn't shorten the others'
  cells. The caller picks its series, EUR or local currency, with or without the benchmark.
- **Histories that don't overlap** grey the constant-weight figures with `NO_OVERLAP` (Labs'
  wording) rather than raise; σ_p and the decomposition grey through their own minimums.
- **Rolling 1Y volatility and Sharpe (RK-24)** run on the same weekly mix, over its whole
  common history whatever the requested window, because a rolling view is for watching a figure
  move through time. Its monthly windows come from the daily mix, as ex-post's come from the
  daily TWR, so the last point is the 1Y constant-weight figure. `mix_weekly` is that weekly
  mix, which a rolling correlation against "the portfolio" uses.
- **`mix_report` is the report without the matrix and the rolling figures**, which the what-if
  (RK-19) measures twice, before and after; `ex_ante_report` is `mix_report` plus both.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from portfolio.risk.correlation import PairwiseCorrelation, pairwise_correlation
from portfolio.risk.ex_ante import (
    ExAnteDecomposition,
    constant_weight_series,
    decomposition_figures,
)
from portfolio.risk.ex_post import SeriesFigures, greyed_series_figures, series_figures
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.rolling import RollingFigures, monthly_windows, rolling_weekly
from portfolio.risk.types import ReturnSeries, Window
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import (
    StandardWindow,
    common_span,
    latest_common_date,
    standard_windows,
)

LABEL = "ex-ante · constant weights"
NO_OVERLAP = "the holdings' histories don't overlap"  # labs/risk.py's wording
_EUR = "EUR"


@dataclass(frozen=True)
class MixReport:
    """One mix's ex-ante figures without the correlation matrix or the rolling figures: what the
    what-if (RK-19) measures twice, before and after."""

    requested: Window  # the standard window asked for, ending on the latest common date
    window: Window | None  # the effective one; None when the histories don't overlap
    limited_by: tuple[str, ...]  # the holding(s) whose start shortened the window
    decomposition: ExAnteDecomposition
    constant_weight: SeriesFigures
    # The constant-weight mix over the holdings' common span, daily and weekly; None when they
    # don't overlap.
    mix_daily: ReturnSeries | None
    mix_weekly: ReturnSeries | None


@dataclass(frozen=True)
class ExAnteReport:
    requested: Window  # the standard window asked for, ending on the latest common date
    window: Window | None  # the effective one; None when the histories don't overlap
    limited_by: tuple[str, ...]  # the holding(s) whose start shortened the window
    decomposition: ExAnteDecomposition
    constant_weight: SeriesFigures
    correlation: PairwiseCorrelation
    # The constant-weight weekly mix over the holdings' common span; None when they don't overlap.
    mix_weekly: ReturnSeries | None
    rolling: RollingFigures  # over the mix's whole history; empty when they don't overlap


def ex_ante_report(
    daily: Mapping[str, ReturnSeries],
    weights: Mapping[str, float],
    label: StandardWindow,
    *,
    currencies: Mapping[str, str],
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
    correlation_daily: Mapping[str, ReturnSeries],
) -> ExAnteReport:
    """`daily` is each holding's daily EUR returns, `weights` their normalised weights (each 0
    or more, summing to 1), `currencies` each one's FX currency. `rates` and `first_published`
    are None together when there's no risk-free series. `correlation_daily` is the matrix's
    daily series, keyed in display order."""
    mix = mix_report(
        daily, weights, label, currencies=currencies, rates=rates, first_published=first_published
    )
    correlation = pairwise_correlation(
        {key: to_weekly(series) for key, series in correlation_daily.items()}, mix.requested
    )
    if mix.mix_daily is None:
        rolling = RollingFigures((), ())
    else:
        rolling = rolling_weekly(
            mix.mix_weekly,
            monthly_windows(mix.mix_daily),
            rates=rates,
            first_published=first_published,
        )
    return ExAnteReport(
        mix.requested,
        mix.window,
        mix.limited_by,
        mix.decomposition,
        mix.constant_weight,
        correlation,
        mix.mix_weekly,
        rolling,
    )


def mix_report(
    daily: Mapping[str, ReturnSeries],
    weights: Mapping[str, float],
    label: StandardWindow,
    *,
    currencies: Mapping[str, str],
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
) -> MixReport:
    """`ex_ante_report` without the matrix and the rolling figures; the arguments are the same."""
    if not daily:
        raise ValueError("an ex-ante report needs at least one holding")
    end = latest_common_date(*daily.values())
    oldest = min(series.base_date for series in daily.values())
    requested = next(w for w in standard_windows(end, oldest) if w.label == label)

    weekly = {key: to_weekly(series) for key, series in daily.items()}
    decomposition = decomposition_figures(weekly, weights, requested)

    if common_span(*daily.values()) is None:
        return MixReport(
            requested,
            None,
            (),
            decomposition,
            greyed_series_figures(requested, NO_OVERLAP),
            None,
            None,
        )

    mix_daily = constant_weight_series(daily, weights)
    # With no week in the panel, its window is the requested one's whole weeks, which can start
    # before the mix does.
    start = max(decomposition.sigma_p.window_start, mix_daily.base_date)
    window = Window(label, start, end)
    mix_weekly = constant_weight_series(weekly, weights)
    nonsync = not (len(daily) == 1 and currencies.get(next(iter(daily))) == _EUR)
    figures = series_figures(
        mix_daily,
        mix_weekly,
        window,
        trading_days=[set(series.dates) for series in daily.values()],
        nonsync=nonsync,
        rates=rates,
        first_published=first_published,
    )
    return MixReport(
        requested, window, decomposition.limited_by, decomposition, figures, mix_daily, mix_weekly
    )
