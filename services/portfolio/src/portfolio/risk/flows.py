"""The I/O around the risk library (RK-11: ex-post). The only module in risk/ that does I/O or
logs, or imports from the rest of portfolio; everything else in the package stays pure, so the
spec's golden tests run against the library alone.

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
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from portfolio.benchmark.base import (
    DEFAULT_BENCHMARK,
    BenchmarkStatus,
    BenchmarkUnavailable,
    status_of,
    unavailable_reason,
)
from portfolio.benchmark.flows import fetch_benchmark
from portfolio.datastore_client.base import DataStoreClient
from portfolio.risk.ex_post import WindowFigures, ex_post_figures
from portfolio.risk.relative import BenchmarkReturns
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.types import Frequency, ReturnSeries, RiskFigure
from portfolio.twr import flows as twr_flows
from portfolio.twr.flows import ExcludedHolding

logger = logging.getLogger(__name__)

LABEL = "ex-post · invested assets"
_RF_LOOKBACK = timedelta(days=14)


@dataclass(frozen=True)
class ExPostRisk:
    label: str
    base_date: date | None  # the TWR's level 1; None when there's nothing to measure
    end_date: date | None  # every window's end
    rf_available: bool
    benchmark: BenchmarkStatus
    windows: tuple[WindowFigures, ...]
    excluded: list[ExcludedHolding]  # from the TWR: by ticker, their weight went to the rest


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
        not_fetched = BenchmarkStatus(benchmark, False, "not fetched: nothing to measure", None)
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
    rates, first_published = _risk_free(datastore_client, daily.base_date, daily.last_date)
    fetched = fetch_benchmark(
        datastore_client, benchmark, daily.last_date, context="ex-post-risk"
    )
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
    return ExPostRisk(
        label=LABEL,
        base_date=daily.base_date,
        end_date=daily.last_date,
        rf_available=rates is not None,
        benchmark=status_of(fetched),
        windows=windows,
        excluded=twr.excluded,
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


def figures_of(window: WindowFigures) -> dict[str, RiskFigure]:
    """Every figure a window reports, by its response name."""
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
    datastore_client: DataStoreClient, start: date, end: date
) -> tuple[list[RatePoint] | None, date | None]:
    try:
        series = datastore_client.get_risk_free_series(start - _RF_LOOKBACK, end)
    except Exception as exc:
        logger.error(
            "ex-post-risk: couldn't fetch the risk-free series, so downside deviation, Sharpe and "
            "Sortino are greyed: %s",
            exc,
            exc_info=exc,
        )
        return None, None
    if series is None:
        logger.warning(
            "ex-post-risk: data-store has no risk-free data stored; downside deviation, Sharpe "
            "and Sortino are greyed."
        )
        return None, None
    rates = [RatePoint(point.date, float(point.rate)) for point in series.points]
    return rates, series.first_date


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
