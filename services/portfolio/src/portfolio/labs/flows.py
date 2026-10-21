"""The I/O around labs/calculator.py (LB-4): fetch each asset's ticker info, price series and FX
series from data-store, then hand what was fetched to compute_backtest.

Orchestration, the same layer as holdings/flows.py, kept out of pipeline.py for the same reason
(near the 64KB limit) and because a backtest touches no database at all: a lab is not stored, and
every input is data-store's. See docs/architecture.md's "Labs orchestration" note.

The fetch itself -- two concurrent phases, a failure isolated to the assets it belongs to -- is
eur_returns/fetch.py's, shared with the portfolio TWR (RK-10). This module words each asset's
status and logs it.

The risk section (RK-8) needs one more call, the risk-free series, over the span the backtest
turned out to have, so it follows the price fetch rather than joining it. It can't fail the
backtest: no rates stored, or a failed call, greys the figures that subtract rf and is logged.
So does the benchmark (RK-13), fetched by benchmark/flows.py: an unknown or unreachable one greys
the relative figures, never the backtest.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio.benchmark.base import DEFAULT_BENCHMARK
from portfolio.benchmark.flows import fetch_benchmark
from portfolio.datastore_client.base import DataStoreClient, RiskFreeSeries
from portfolio.eur_returns import fetch
from portfolio.eur_returns.fetch import Fetched
from portfolio.labs.calculator import (
    NO_FX_RATE,
    NO_HISTORY,
    AssetInput,
    BacktestResult,
    Period,
    compute_backtest,
)
from portfolio.labs.risk import (
    NO_OVERLAP,
    BacktestRisk,
    RiskFree,
    RiskFreeMissing,
    WindowRisk,
    compute_backtest_risk,
    risk_free_span,
)
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.types import RiskFigure
from portfolio.risk.windows import StandardWindow

logger = logging.getLogger(__name__)

# Max needs each asset's whole history, and data-store's series endpoints require a start.
EPOCH = date(1970, 1, 1)

INCLUDED = "included"
NOT_TRACKED = fetch.NOT_TRACKED
NO_HISTORY_STATUS = fetch.NO_HISTORY
NO_FX_RATE_STATUS = fetch.NO_FX_RATE
FAILED = fetch.FAILED

_REASONS = {
    NOT_TRACKED: "not tracked",
    NO_HISTORY_STATUS: NO_HISTORY,
    NO_FX_RATE_STATUS: NO_FX_RATE,
    # The exception itself goes to the log: it names internal URLs, and the user can only act on
    # "try again later".
    FAILED: "couldn't reach data-store",
}
# compute_backtest's own Unavailable reasons, mapped back to a status.
_CALCULATOR_STATUSES = {NO_HISTORY: NO_HISTORY_STATUS, NO_FX_RATE: NO_FX_RATE_STATUS}

NO_RISK_FREE = "no risk-free rate data"
RISK_FREE_FAILED = _REASONS[FAILED]


@dataclass(frozen=True)
class AssetStatus:
    ticker: str
    status: str  # included, not_tracked, no_history, no_fx_rate or failed
    reason: str | None  # None when included


@dataclass(frozen=True)
class LabsBacktest:
    result: BacktestResult
    statuses: list[AssetStatus]  # every requested asset, in input order
    risk: BacktestRisk | None = None  # None exactly when no asset is included


def run_backtest(
    datastore_client: DataStoreClient,
    mix: Sequence[tuple[str, Decimal]],
    period: Period,
    *,
    benchmark: str = DEFAULT_BENCHMARK,
    today: date | None = None,
) -> LabsBacktest:
    """Backtest `mix` (ticker, weight) pairs, with the growth series for `period` and the risk
    table measured against `benchmark`. The mix is assumed valid (non-empty, unique tickers,
    positive weights): the API checks that first."""
    today = today or date.today()
    fetched_or_not = fetch.fetch_series(
        datastore_client, [ticker for ticker, _ in mix], EPOCH, today
    )
    left_out: dict[str, tuple[str, BaseException | None]] = {
        ticker: (outcome.status, outcome.exc)
        for ticker, outcome in fetched_or_not.items()
        if not isinstance(outcome, Fetched)
    }
    fetched = {
        ticker: outcome
        for ticker, outcome in fetched_or_not.items()
        if isinstance(outcome, Fetched)
    }

    inputs = [
        AssetInput(
            ticker=ticker,
            weight=weight,
            prices=fetched[ticker].prices,
            fx=fetched[ticker].fx,
        )
        for ticker, weight in mix
        if ticker in fetched
    ]
    if inputs:
        result = compute_backtest(inputs, growth_period=period)
    else:
        result = BacktestResult(end_date=None, mix=None, assets=[], unavailable=[], growth=None)
    for unavailable in result.unavailable:
        left_out[unavailable.ticker] = (_CALCULATOR_STATUSES[unavailable.reason], None)

    statuses = []
    for ticker, _ in mix:
        if ticker in left_out:
            status, exc = left_out[ticker]
            _log_left_out(ticker, status, exc)
            statuses.append(AssetStatus(ticker, status, _REASONS[status]))
        else:
            info = fetched[ticker].info
            logger.info(
                "backtest: %s: included, %d day(s) of history, in %s.",
                ticker,
                len(fetched[ticker].prices),
                info.currency or info.fx_currency,
            )
            statuses.append(AssetStatus(ticker, INCLUDED, None))

    risk = _risk(datastore_client, inputs, result, benchmark, today) if inputs else None
    return LabsBacktest(result=result, statuses=statuses, risk=risk)


def _risk(
    datastore_client: DataStoreClient,
    inputs: Sequence[AssetInput],
    result: BacktestResult,
    benchmark_ticker: str,
    today: date,
) -> BacktestRisk | None:
    span = risk_free_span(result)
    if span is None:
        risk_free = RiskFreeMissing(NO_OVERLAP)
    else:
        risk_free = _fetch_risk_free(datastore_client, *span)
    benchmark = fetch_benchmark(datastore_client, benchmark_ticker, today, context="backtest risk")
    risk = compute_backtest_risk(inputs, risk_free, result.growth, benchmark)
    if risk is not None:
        _log_risk(risk)
    return risk


def _fetch_risk_free(
    datastore_client: DataStoreClient, start: date, end: date
) -> RiskFree | RiskFreeMissing:
    try:
        series: RiskFreeSeries | None = datastore_client.get_risk_free_series(start, end)
    except Exception as exc:
        logger.error(
            "backtest risk: the risk-free fetch failed, so downside deviation, Sharpe and "
            "Sortino are greyed -- a data-store call raised: %s",
            exc,
            exc_info=exc,
        )
        return RiskFreeMissing(RISK_FREE_FAILED)
    if series is None:
        logger.warning(
            "backtest risk: data-store has no risk-free rates stored, so downside deviation, "
            "Sharpe and Sortino are greyed."
        )
        return RiskFreeMissing(NO_RISK_FREE)
    logger.info(
        "backtest risk: risk-free rate: %d point(s) for %s..%s, series from %s.",
        len(series.points),
        start,
        end,
        series.first_date,
    )
    return RiskFree(
        rates=[RatePoint(point.date, float(point.rate)) for point in series.points],
        first_published=series.first_date,
    )


def _log_risk(risk: BacktestRisk) -> None:
    if risk.common_start is None:
        logger.info(
            "backtest risk: %d asset(s), ending %s: %s, so no window is measured.",
            len(risk.assets),
            risk.end_date,
            NO_OVERLAP,
        )
    else:
        logger.info(
            "backtest risk: %d asset(s), windows end %s, common start %s (limited by %s).",
            len(risk.assets),
            risk.end_date,
            risk.common_start,
            ", ".join(risk.limited_by),
        )
    if not risk.risk_free.available and risk.common_start is not None:
        logger.info("backtest risk: excess-return figures greyed: %s.", risk.risk_free.reason)

    for label, window in risk.mix.windows.items():
        if not window.covered:
            logger.info("backtest risk: mix %s: not covered, %s.", label, window.reason)
        else:
            greyed = sum(1 for figure in _figures(window) if figure.value is None)
            logger.info(
                "backtest risk: mix %s: measured %s..%s, %d of %d figure(s) greyed.",
                label,
                window.start,
                window.end,
                greyed,
                len(_figures(window)),
            )
    for row in risk.assets:
        logger.info(
            "backtest risk: %s: measured in %d of %d window(s).",
            row.ticker,
            _measured(row.windows),
            len(row.windows),
        )
    benchmark = risk.benchmark
    logger.info(
        "backtest risk: done, the mix is measured in %d of %d window(s) against %s, %d asset "
        "row(s)%s.",
        _measured(risk.mix.windows),
        len(risk.mix.windows),
        (
            benchmark.ticker
            if benchmark.available
            else f"{benchmark.ticker} (unavailable: {benchmark.reason})"
        ),
        len(risk.assets),
        (
            ""
            if risk.drawdown is None
            else f", drawdown series of {len(risk.drawdown.dates)} point(s)"
        ),
    )


def _figures(window: WindowRisk) -> list[RiskFigure | None]:
    return [
        window.cumulative_return,
        window.cagr,
        window.volatility,
        window.downside_deviation,
        window.sharpe,
        window.sortino,
        window.calmar,
        window.max_drawdown,
        window.var_95,
        window.cvar_95,
        window.var_99,
        window.cvar_99,
        *(
            ()
            if window.relative is None
            else (
                window.relative.beta,
                window.relative.correlation,
                window.relative.tracking_error,
                window.relative.information_ratio,
            )
        ),
    ]


def _measured(windows: Mapping[StandardWindow, WindowRisk]) -> int:
    return sum(1 for window in windows.values() if window.covered)


def _log_left_out(ticker: str, status: str, exc: BaseException | None) -> None:
    if status == FAILED:
        logger.error(
            "backtest: %s: failed, left out -- a data-store call raised: %s",
            ticker,
            exc,
            exc_info=exc,
        )
    else:
        logger.warning("backtest: %s: left out, %s.", ticker, _REASONS[status])
