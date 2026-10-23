"""The I/O around the risk library: ex-post (RK-11), ex-ante (RK-17) and the candidate what-if
(RK-19). The only module in
risk/ that does I/O or logs, or imports from the rest of portfolio; everything else in the
package stays pure, so the spec's golden tests run against the library alone.

Ex-post risk (spec §6.1) is measured on the user's daily TWR of invested positions, from
twr/flows.py, which reads the accounts, fetches every held ticker's series and names each one it
leaves out. This flow adds the risk-free series, converts both to the library's floats, and hands
them to risk/ex_post.py.

- **The risk-free fetch starts two weeks before the base date.** `risk_free_returns`
  forward-fills each day from the latest rate on or before it, and refuses a day with none, so a
  base date after a weekend or a holiday needs an earlier published point.
- **No risk-free data is not rf = 0.** When data-store has none stored (RK-4's None) or the call
  fails, the measures on excess returns are greyed and everything else is still measured. A
  failure logs its exception here; the response only says the rate was unavailable.
- **The benchmark** (RK-13) is fetched by benchmark/flows.py over its whole history, up to the
  TWR's last date, and handed to the library as its weekly returns. An unknown or unreachable
  one greys the relative figures with its reason; everything else is still measured.
- **Nothing to measure is said so.** With no TWR returns (no rows yet, or every holding left
  out), there are no windows, no risk-free fetch and no benchmark fetch.
- **Rolling 1Y volatility and Sharpe (RK-24)** run on the TWR's weekly returns, monthly, over its
  whole history (risk/rolling.py), from the same risk-free fetch.

Ex-ante risk (spec §6.2-6.3, §7.1) is measured on a mix the caller passes, as Labs' backtest
is: web builds it from the holdings (`labs_view.build_default_mix`), so the weights are worked
out in one place. No user data is read and nothing is stored.

- **Every requested asset gets a status**, in input order: included, excluded (by the request's
  `exclude`, before any fetch), or left out with eur_returns/eligibility.py's reason. The weights
  are normalised over the included holdings.
- **The holdings are fetched over their whole history**, as Labs' are, so Max can reach the
  oldest one's first day.
- **The risk-free fetch starts two weeks before the oldest holding's first day**, as ex-post's
  does before its base date: one call covers whichever window the report settles on.
- **The correlation matrix** is the holdings plus the benchmark, in EUR or, on request, each
  ticker's local-currency returns (§7.1's toggle). σ_p and the decomposition are always EUR. A
  benchmark that is also a holding isn't repeated; an unavailable one is left out of the matrix,
  and the response says why.
- **A rolling correlation (RK-24) is asked for by pair**: two tickers from the mix or the
  benchmark, or one ticker against the portfolio, which is the constant-weight weekly mix. It is
  always in EUR, whatever the matrix's currency, because the portfolio is EUR. A ticker that
  can't be measured (left out, excluded, or an unavailable benchmark) gives no points and says
  why. It never fails the request.

The what-if (spec §7.2) takes the same mix plus a candidate, a weight and a funding rule, and
hands them to risk/what_if.py. No benchmark and no matrix: its rows don't use them.

- **One fetch covers the holdings and the candidate**, over their whole history, then one
  risk-free fetch from two weeks before the oldest of them, as ex-ante does.
- **The holdings get ex-ante's statuses and the candidate its own.** Their weights are
  normalised over the included holdings and then funded, so `w_c` is a share of the whole mix
  after the change.
- **No comparison is said so, not raised**: an unmeasurable candidate, no included holding, or
  an unmeasurable replaced holding (`funding_reason`) gives no report and no risk-free fetch.
  The API has already refused the malformed requests (a candidate already held, a replace
  target outside the mix or excluded).
- **R14's survivorship flag** is set when data-store prices the candidate only through a fund's
  holdings (`TickerInfo.status` None, eligibility.py's `untracked`): it is an index constituent
  the owner never tracked, so its history comes from today's index members.
"""

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from portfolio.benchmark.base import (
    DEFAULT_BENCHMARK,
    Benchmark,
    BenchmarkStatus,
    BenchmarkUnavailable,
    status_of,
    unavailable_reason,
)
from portfolio.benchmark.flows import EPOCH, fetch_benchmark
from portfolio.datastore_client.base import DataStoreClient
from portfolio.eur_returns.calculator import EurReturns, eur_daily_returns
from portfolio.eur_returns.eligibility import fetch_eur_returns
from portfolio.risk.ex_ante_report import LABEL as EX_ANTE_LABEL
from portfolio.risk.ex_ante_report import NO_OVERLAP, ExAnteReport, ex_ante_report
from portfolio.risk.ex_post import SeriesFigures, WindowFigures, ex_post_figures
from portfolio.risk.relative import BenchmarkReturns
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.rolling import (
    RollingFigures,
    monthly_windows,
    rolling_correlation,
    rolling_weekly,
)
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure
from portfolio.risk.weekly import to_weekly
from portfolio.risk.what_if import PRO_RATA_RULE, Funding, WhatIfReport, what_if_report
from portfolio.risk.windows import StandardWindow
from portfolio.twr import flows as twr_flows
from portfolio.twr.flows import ExcludedHolding

logger = logging.getLogger(__name__)

LABEL = "ex-post · invested assets"
_RF_LOOKBACK = timedelta(days=14)

INCLUDED = "included"
EXCLUDED = "excluded"
EXCLUDED_REASON = "excluded by request"
EUR_RETURNS = "eur"
LOCAL_RETURNS = "local"
PORTFOLIO = "portfolio"  # a rolling pair's other side when it is the constant-weight mix
_NOTHING_TO_MEASURE = "not fetched: nothing to measure"


@dataclass(frozen=True)
class ExPostRisk:
    label: str
    base_date: date | None  # the TWR's level 1; None when there's nothing to measure
    end_date: date | None  # every window's end
    rf_available: bool
    benchmark: BenchmarkStatus
    windows: tuple[WindowFigures, ...]
    excluded: list[ExcludedHolding]  # from the TWR: by ticker, their weight went to the rest
    rolling: RollingFigures | None = None  # None exactly when there's nothing to measure


def compute_ex_post(
    conn,
    datastore_client: DataStoreClient,
    *,
    user_id: UUID,
    benchmark: str = DEFAULT_BENCHMARK,
    today: date | None = None,
) -> ExPostRisk:
    """This user's ex-post risk figures over the standard windows, with relative figures against
    `benchmark`. Read-only, like the TWR."""
    twr = twr_flows.compute_portfolio_twr(conn, datastore_client, user_id=user_id, today=today)
    if not twr.points:
        logger.info("ex-post-risk: no TWR returns to measure, so no windows.")
        not_fetched = BenchmarkStatus(benchmark, False, _NOTHING_TO_MEASURE, None)
        return ExPostRisk(LABEL, None, None, False, not_fetched, (), twr.excluded)

    daily = ReturnSeries(
        Frequency.DAILY,
        twr.base_date,
        tuple(p.price_date for p in twr.points),
        tuple(None if p.twr_return is None else float(p.twr_return) for p in twr.points),
    )
    logger.info(
        "ex-post-risk: measuring %d day(s) of TWR from %s to %s, %d ticker(s), against %s.",
        len(daily.dates),
        daily.base_date,
        daily.last_date,
        len(twr.currencies),
        benchmark,
    )
    rates, first_published = _risk_free(
        datastore_client, daily.base_date, daily.last_date, context="ex-post-risk"
    )
    fetched = fetch_benchmark(datastore_client, benchmark, daily.last_date, context="ex-post-risk")
    if isinstance(fetched, BenchmarkUnavailable):
        against: BenchmarkReturns | str = unavailable_reason(fetched)
    else:
        against = BenchmarkReturns(fetched.first_date, fetched.weekly)
    windows = ex_post_figures(
        daily,
        traded_days={d.price_date for d in twr.days if d.traded},
        held={d.price_date: d.tickers for d in twr.days},
        currencies=twr.currencies,
        rates=rates,
        first_published=first_published,
        benchmark=against,
    )
    for window in windows:
        _log_window(window, benchmark)
    rolling = rolling_weekly(
        to_weekly(daily), monthly_windows(daily), rates=rates, first_published=first_published
    )
    _log_rolling(rolling, context="ex-post-risk")
    return ExPostRisk(
        label=LABEL,
        base_date=daily.base_date,
        end_date=daily.last_date,
        rf_available=rates is not None,
        benchmark=status_of(fetched),
        windows=windows,
        excluded=twr.excluded,
        rolling=rolling,
    )


@dataclass(frozen=True)
class AssetStatus:
    ticker: str
    status: str  # included, excluded, not_tracked, no_history, no_fx_rate or failed
    reason: str | None  # None when included


@dataclass(frozen=True)
class RollingCorrelation:
    """A requested pair's rolling 52-week correlation. `points` is empty exactly when `reason`
    says why one side can't be measured."""

    a: str
    b: str  # a ticker, or PORTFOLIO
    points: tuple[RiskFigure, ...]
    reason: str | None


@dataclass(frozen=True)
class ExAnteRisk:
    label: str
    currency: str  # the correlation matrix's returns: eur or local
    rf_available: bool
    benchmark: BenchmarkStatus
    benchmark_key: str | None  # the benchmark's row in the matrix; None when it has none
    statuses: list[AssetStatus]  # every requested asset, in input order
    weights: dict[str, float]  # the included holdings', normalised
    report: ExAnteReport | None  # None exactly when no holding is included
    # None when no pair was asked for, or no holding is included.
    rolling_correlation: RollingCorrelation | None = None


def compute_ex_ante(
    datastore_client: DataStoreClient,
    mix: Sequence[tuple[str, Decimal]],
    *,
    window: StandardWindow = StandardWindow.Y3,
    benchmark: str = DEFAULT_BENCHMARK,
    currency: str = EUR_RETURNS,
    exclude: Collection[str] = (),
    rolling_pair: tuple[str, str | None] | None = None,
    today: date | None = None,
) -> ExAnteRisk:
    """Ex-ante risk of `mix` (ticker, weight) pairs over `window`, without the tickers in
    `exclude`, with the correlation matrix in `currency` against `benchmark`. `rolling_pair` asks
    for a rolling correlation of two tickers, or of one against the portfolio (None). The mix is
    assumed valid (non-empty, unique tickers, positive weights, `exclude` a strict subset, the
    pair's tickers in the mix or the benchmark): the API checks that first."""
    today = today or date.today()
    excluded = set(exclude)
    logger.info(
        "ex-ante-risk: %d asset(s), %d excluded by request, window %s, against %s, correlation "
        "in %s.",
        len(mix),
        len(excluded),
        window,
        benchmark,
        currency,
    )
    for ticker, _ in mix:
        if ticker in excluded:
            logger.info("ex-ante-risk: %s: %s.", ticker, EXCLUDED_REASON)
    eligible = fetch_eur_returns(
        datastore_client,
        [ticker for ticker, _ in mix if ticker not in excluded],
        EPOCH,
        today,
        logger=logger,
        context="ex-ante-risk",
    )
    left_out = {holding.ticker: holding for holding in eligible.excluded}
    statuses = [_status(ticker, excluded, left_out) for ticker, _ in mix]

    if not eligible.returns:
        logger.info("ex-ante-risk: no holding could be included, so nothing to measure.")
        not_fetched = BenchmarkStatus(benchmark, False, _NOTHING_TO_MEASURE, None)
        return ExAnteRisk(EX_ANTE_LABEL, currency, False, not_fetched, None, statuses, {}, None)

    included = [(ticker, weight) for ticker, weight in mix if ticker in eligible.returns]
    total = sum(weight for _, weight in included)
    weights = {ticker: float(weight / total) for ticker, weight in included}
    daily = {ticker: _series(eligible.returns[ticker], local=False) for ticker, _ in included}

    oldest = min(series.base_date for series in daily.values())
    end = min(series.last_date for series in daily.values())
    rates, first_published = _risk_free(datastore_client, oldest, end, context="ex-ante-risk")
    fetched = fetch_benchmark(datastore_client, benchmark, today, context="ex-ante-risk")

    local = currency == LOCAL_RETURNS
    correlation_daily = (
        {ticker: _series(eligible.returns[ticker], local=True) for ticker, _ in included}
        if local
        else dict(daily)
    )
    if not isinstance(fetched, BenchmarkUnavailable) and benchmark not in correlation_daily:
        correlation_daily[benchmark] = _benchmark_series(fetched, local=local)

    report = ex_ante_report(
        daily,
        weights,
        window,
        currencies=eligible.currencies,
        rates=rates,
        first_published=first_published,
        correlation_daily=correlation_daily,
    )
    _log_ex_ante(report, len(included), len(left_out))
    _log_rolling(report.rolling, context="ex-ante-risk")
    pair = None
    if rolling_pair is not None:
        pair = _rolling_pair(
            rolling_pair,
            daily=daily,
            statuses=statuses,
            benchmark=fetched,
            mix_weekly=report.mix_weekly,
        )
        _log_rolling_pair(pair)
    return ExAnteRisk(
        label=EX_ANTE_LABEL,
        currency=currency,
        rf_available=rates is not None,
        benchmark=status_of(fetched),
        benchmark_key=benchmark if benchmark in correlation_daily else None,
        statuses=statuses,
        weights=weights,
        report=report,
        rolling_correlation=pair,
    )


def _rolling_pair(
    pair: tuple[str, str | None],
    *,
    daily: dict[str, ReturnSeries],
    statuses: list[AssetStatus],
    benchmark: Benchmark | BenchmarkUnavailable,
    mix_weekly: ReturnSeries | None,
) -> RollingCorrelation:
    a, b = pair
    sides = [
        _pair_side(key, daily=daily, statuses=statuses, benchmark=benchmark, mix_weekly=mix_weekly)
        for key in (a, b)
    ]
    label = PORTFOLIO if b is None else b
    reasons = [side for side in sides if isinstance(side, str)]
    if reasons:
        return RollingCorrelation(a, label, (), reasons[0])
    return RollingCorrelation(a, label, rolling_correlation(*sides), None)


def _pair_side(
    key: str | None,
    *,
    daily: dict[str, ReturnSeries],
    statuses: list[AssetStatus],
    benchmark: Benchmark | BenchmarkUnavailable,
    mix_weekly: ReturnSeries | None,
) -> ReturnSeries | str:
    """One side's weekly EUR returns, or why it has none."""
    if key is None:
        return mix_weekly if mix_weekly is not None else f"{PORTFOLIO} not included: {NO_OVERLAP}"
    if key in daily:
        return to_weekly(daily[key])
    if key == benchmark.ticker:
        if isinstance(benchmark, BenchmarkUnavailable):
            return f"{key} not included: {unavailable_reason(benchmark)}"
        return benchmark.weekly
    status = next((s for s in statuses if s.ticker == key), None)
    if status is None:
        raise ValueError(f"{key} is neither in the mix nor the benchmark")
    return f"{key} not included: {status.reason}"


@dataclass(frozen=True)
class WhatIfRisk:
    label: str
    rf_available: bool
    statuses: list[AssetStatus]  # every requested holding, in input order
    candidate: AssetStatus
    survivorship: bool  # R14: the candidate is priced only through a fund's holdings
    funding_reason: str | None  # why the replaced holding can't fund the candidate
    weights: dict[str, float]  # the included holdings', normalised: "before"
    report: WhatIfReport | None  # None when there's nothing to compare


def compute_what_if(
    datastore_client: DataStoreClient,
    mix: Sequence[tuple[str, Decimal]],
    candidate: str,
    weight: float,
    funding: Funding,
    *,
    window: StandardWindow = StandardWindow.Y3,
    exclude: Collection[str] = (),
    today: date | None = None,
) -> WhatIfRisk:
    """`mix` (ticker, weight) pairs, without the tickers in `exclude`, before and after adding
    `candidate` at `weight` (a fraction of the whole mix) under `funding`, over `window`. The
    request is assumed valid (the candidate not in the mix, `weight` in (0, 1), a replace
    target in the mix and not excluded): the API checks that first."""
    today = today or date.today()
    excluded = set(exclude)
    logger.info(
        "what-if: %d holding(s), %d excluded by request, candidate %s at %.1f%%, funded %s, "
        "window %s.",
        len(mix),
        len(excluded),
        candidate,
        weight * 100,
        funding.rule if funding.ticker is None else f"{funding.rule} {funding.ticker}",
        window,
    )
    for ticker, _ in mix:
        if ticker in excluded:
            logger.info("what-if: %s: %s.", ticker, EXCLUDED_REASON)
    eligible = fetch_eur_returns(
        datastore_client,
        [ticker for ticker, _ in mix if ticker not in excluded] + [candidate],
        EPOCH,
        today,
        logger=logger,
        context="what-if",
    )
    left_out = {holding.ticker: holding for holding in eligible.excluded}
    statuses = [_status(ticker, excluded, left_out) for ticker, _ in mix]
    candidate_status = _status(candidate, (), left_out)
    survivorship = candidate in eligible.untracked
    included = [(ticker, w) for ticker, w in mix if ticker in eligible.returns]

    def nothing(funding_reason: str | None = None) -> WhatIfRisk:
        return WhatIfRisk(
            EX_ANTE_LABEL,
            False,
            statuses,
            candidate_status,
            survivorship,
            funding_reason,
            {},
            None,
        )

    if candidate not in eligible.returns:
        logger.warning(
            "what-if: the candidate %s can't be measured (%s), so nothing to compare.",
            candidate,
            candidate_status.reason,
        )
        return nothing()
    if not included:
        logger.warning("what-if: no holding could be included, so nothing to compare.")
        return nothing()
    if funding.rule != PRO_RATA_RULE and funding.ticker in left_out:
        reason = (
            f"{funding.ticker}, the holding to replace, can't be measured: "
            f"{left_out[funding.ticker].reason}"
        )
        logger.warning("what-if: %s, so nothing to compare.", reason)
        return nothing(reason)

    total = sum(w for _, w in included)
    weights = {ticker: float(w / total) for ticker, w in included}
    daily = {
        ticker: _series(eligible.returns[ticker], local=False) for ticker in [*weights, candidate]
    }
    oldest = min(series.base_date for series in daily.values())
    end = min(series.last_date for series in daily.values())
    rates, first_published = _risk_free(datastore_client, oldest, end, context="what-if")

    report = what_if_report(
        daily,
        weights,
        candidate,
        weight,
        funding,
        window,
        currencies=eligible.currencies,
        rates=rates,
        first_published=first_published,
        survivorship=survivorship,
    )
    _log_what_if(report, len(included), sum(1 for s in statuses if s.status != INCLUDED))
    return WhatIfRisk(
        EX_ANTE_LABEL,
        rates is not None,
        statuses,
        candidate_status,
        survivorship,
        None,
        weights,
        report,
    )


def _status(ticker: str, excluded: Collection[str], left_out: dict) -> AssetStatus:
    if ticker in excluded:
        return AssetStatus(ticker, EXCLUDED, EXCLUDED_REASON)
    if ticker in left_out:
        return AssetStatus(ticker, left_out[ticker].status, left_out[ticker].reason)
    return AssetStatus(ticker, INCLUDED, None)


def _log_what_if(report: WhatIfReport, n_included: int, n_left_out: int) -> None:
    after = report.after
    requested = after.requested
    if after.window is None:
        logger.info(
            "what-if: window %s: requested %s..%s, but the histories don't overlap, so the "
            "constant-weight figures are greyed.",
            requested.label,
            requested.start,
            requested.end,
        )
    else:
        without = report.without_candidate
        logger.info(
            "what-if: window %s: measured %s..%s%s.",
            requested.label,
            after.window.start,
            after.window.end,
            (
                f", shortened by the candidate {report.candidate} (the holdings alone: "
                f"{without.start}..{without.end})"
                if report.shortened and without is not None
                else ""
            ),
        )
    sigma_p = report.sigma_p
    threshold = report.threshold
    if threshold.reason is not None:
        threshold_text = f"threshold not shown ({threshold.reason})"
    else:
        threshold_text = (
            f"threshold: candidate Sharpe {threshold.candidate_sharpe.value:.2f} "
            f"{'>' if threshold.improves else '<='} {threshold.threshold:.2f}"
        )
    logger.info(
        "what-if: done, %d holding(s) included, %d left out; delta sigma_p %s; %s; "
        "survivorship %s.",
        n_included,
        n_left_out,
        "greyed" if sigma_p.delta is None else f"{sigma_p.delta * 100:+.2f} pt",
        threshold_text,
        "flagged" if Flag.SURVIVORSHIP in sigma_p.after.flags else "not flagged",
    )


def correlation_cells(report: ExAnteReport) -> list[tuple[str, str, RiskFigure]]:
    """The matrix's upper triangle, diagonal included, row by row: the response sends each pair
    once."""
    keys = report.correlation.keys
    return [(a, b, report.correlation.cell(a, b)) for i, a in enumerate(keys) for b in keys[i:]]


def _series(returns: EurReturns, *, local: bool) -> ReturnSeries:
    values = returns.r_local if local else returns.r_eur
    return ReturnSeries(
        Frequency.DAILY,
        returns.anchor,
        tuple(returns.dates),
        tuple(None if r is None else float(r) for r in values),
    )


def _benchmark_series(benchmark: Benchmark, *, local: bool) -> ReturnSeries:
    if not local:
        return benchmark.daily
    # build_benchmark already converted these once, so they're eligible.
    returns = eur_daily_returns(benchmark.fetched.prices, benchmark.fetched.fx)
    return _series(returns, local=True)


def _log_ex_ante(report: ExAnteReport, n_included: int, n_left_out: int) -> None:
    requested = report.requested
    span = f"{requested.label}: requested {requested.start}..{requested.end}"
    if report.window is None:
        logger.info(
            "ex-ante-risk: window %s, but the holdings' histories don't overlap, so the "
            "constant-weight figures are greyed.",
            span,
        )
    else:
        logger.info(
            "ex-ante-risk: window %s, measured %s..%s%s.",
            span,
            report.window.start,
            report.window.end,
            f", limited by {', '.join(report.limited_by)}" if report.limited_by else "",
        )
    figures = figures_of(report.constant_weight).values()
    cells = correlation_cells(report)
    sigma_p = report.decomposition.sigma_p
    logger.info(
        "ex-ante-risk: done, %d holding(s) included, %d left out; sigma_p %s; %d of %d "
        "constant-weight figure(s) greyed; %d of %d correlation cell(s) greyed.",
        n_included,
        n_left_out,
        "measured" if sigma_p.value is not None else f"greyed ({sigma_p.reason})",
        sum(1 for figure in figures if figure.value is None),
        len(figures),
        sum(1 for _, _, figure in cells if figure.value is None),
        len(cells),
    )


def relative_of(window: WindowFigures) -> dict[str, RiskFigure]:
    """A window's figures against the benchmark, by their response name."""
    relative = window.relative
    return {
        "beta": relative.beta,
        "correlation": relative.correlation,
        "tracking_error": relative.tracking_error,
        "information_ratio": relative.information_ratio,
    }


def figures_of(window: WindowFigures | SeriesFigures) -> dict[str, RiskFigure]:
    """Every §5.1-5.5 figure a window (or ex-ante's constant-weight mix) reports, by its response
    name."""
    return {
        "cumulative_return": window.returns.cumulative_return,
        "cagr": window.returns.cagr,
        "volatility": window.weekly.volatility,
        "downside_deviation": window.weekly.downside_deviation,
        "sharpe": window.weekly.sharpe,
        "sortino": window.weekly.sortino,
        "max_drawdown": window.drawdown.max_drawdown,
        "calmar": window.calmar,
        "var_95": window.tail.var_95,
        "cvar_95": window.tail.cvar_95,
        "var_99": window.tail.var_99,
        "cvar_99": window.tail.cvar_99,
    }


def _risk_free(
    datastore_client: DataStoreClient, start: date, end: date, *, context: str
) -> tuple[list[RatePoint] | None, date | None]:
    try:
        series = datastore_client.get_risk_free_series(start - _RF_LOOKBACK, end)
    except Exception as exc:
        logger.error(
            "%s: couldn't fetch the risk-free series, so downside deviation, Sharpe and "
            "Sortino are greyed: %s",
            context,
            exc,
            exc_info=exc,
        )
        return None, None
    if series is None:
        logger.warning(
            "%s: data-store has no risk-free data stored; downside deviation, Sharpe "
            "and Sortino are greyed.",
            context,
        )
        return None, None
    rates = [RatePoint(point.date, float(point.rate)) for point in series.points]
    return rates, series.first_date


def _log_rolling(rolling: RollingFigures, *, context: str) -> None:
    n = len(rolling.volatility)
    if not n:
        logger.info("%s: rolling 1Y: no point, the history is under a year.", context)
        return
    logger.info(
        "%s: rolling 1Y: %d monthly point(s); %d of %d volatility and %d of %d Sharpe greyed.",
        context,
        n,
        sum(1 for f in rolling.volatility if f.value is None),
        n,
        sum(1 for f in rolling.sharpe if f.value is None),
        n,
    )


def _log_rolling_pair(pair: RollingCorrelation) -> None:
    if pair.reason is not None:
        logger.info(
            "ex-ante-risk: rolling correlation %s vs %s: no point, %s.", pair.a, pair.b, pair.reason
        )
        return
    logger.info(
        "ex-ante-risk: rolling correlation %s vs %s: %d weekly point(s), %d greyed.",
        pair.a,
        pair.b,
        len(pair.points),
        sum(1 for p in pair.points if p.value is None),
    )


def _log_window(window: WindowFigures, benchmark: str) -> None:
    span = f"{window.window.label} ({window.window.start}..{window.window.end})"
    if not window.covered:
        logger.info(
            "ex-post-risk: window %s: greyed, %s.", span, window.returns.cumulative_return.reason
        )
        return
    figures = figures_of(window).values()
    greyed = sum(1 for figure in figures if figure.value is None)
    relative = relative_of(window).values()
    relative_greyed = sum(1 for figure in relative if figure.value is None)
    logger.info(
        "ex-post-risk: window %s: measured, %d daily obs, %d of %d greyed; against %s, %d of %d "
        "greyed.",
        span,
        window.drawdown.max_drawdown.n_obs,
        greyed,
        len(figures),
        benchmark,
        relative_greyed,
        len(relative),
    )
