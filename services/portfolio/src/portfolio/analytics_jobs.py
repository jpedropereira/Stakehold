"""The read-only analytics jobs: Labs' backtest, ex-post and ex-ante risk, and the look-through.
Each builds a data-store client, runs one flow, logs its summary and returns builtins for the
route. They are jobs.py's kind of runnable unit and follow its conventions (an optional
`settings`, `configure_logging()` first). They live apart because jobs.py crossed the 64 KB file
limit, and these share no helpers with its write jobs except `require_datastore_client`.
"""

import logging
from collections.abc import Collection
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

from portfolio.benchmark.base import DEFAULT_BENCHMARK
from portfolio.jobs import require_datastore_client
from portfolio.labs import flows as labs_flows
from portfolio.labs.calculator import BacktestResult, Period
from portfolio.look_through import flows as look_through_flows
from portfolio.look_through.calculator import DIMENSION_LABELS, Concentration, Dimension
from portfolio.risk import flows as risk_flows
from portfolio.risk.ex_post import WindowFigures
from portfolio.risk.types import RiskFigure
from portfolio.risk.what_if import Funding, Row, WhatIfReport
from portfolio.risk.windows import StandardWindow
from portfolio.settings import Settings, load_settings
from shared.db.connection import get_connection
from shared.logging_config import configure_logging

logger = logging.getLogger(__name__)


def get_ex_post_risk(
    *, user_id: UUID, benchmark: str = DEFAULT_BENCHMARK, settings: Settings | None = None
) -> dict:
    """This user's ex-post risk over the standard windows, measured on the TWR of invested
    positions against `benchmark` (see risk/flows.compute_ex_post). Read-only: never triggers a
    rebuild."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = require_datastore_client(settings, command="ex-post risk")
    conn = get_connection(settings.database_url)
    try:
        logger.info("get-ex-post-risk: measuring ex-post risk for user %s.", user_id)
        result = risk_flows.compute_ex_post(
            conn, datastore_client, user_id=user_id, benchmark=benchmark
        )
    finally:
        conn.close()

    if result.windows:
        uncovered = sum(1 for window in result.windows if not window.covered)
        status = result.benchmark
        logger.info(
            "get-ex-post-risk: done, %s to %s: %d window(s) measured, %d greyed for short "
            "history, risk-free rate %s, benchmark %s, %d holding(s) left out.",
            result.base_date,
            result.end_date,
            len(result.windows) - uncovered,
            uncovered,
            "available" if result.rf_available else "unavailable",
            (
                f"{status.ticker} (from {status.first_date})"
                if status.available
                else f"{status.ticker} unavailable ({status.reason})"
            ),
            len(result.excluded),
        )
    elif not result.excluded:
        logger.info(
            "get-ex-post-risk: done, no TWR returns in any account yet, so nothing to measure."
        )
    else:
        logger.warning(
            "get-ex-post-risk: done, nothing to measure: %d holding(s) left out, and the rest "
            "gave no TWR returns.",
            len(result.excluded),
        )
    return {
        "label": result.label,
        "base_date": result.base_date,
        "end_date": result.end_date,
        "rf_available": result.rf_available,
        "benchmark": asdict(result.benchmark),
        "windows": [_ex_post_window(window) for window in result.windows],
        "n_excluded": len(result.excluded),
        "excluded": [asdict(holding) for holding in result.excluded],
    }


def _ex_post_window(window: WindowFigures) -> dict:
    """One window as builtins. The drawdown's DD series stays out: RK-12 shows the summary only
    (agreed for RK-11)."""
    return {
        "label": str(window.window.label),
        "start": window.window.start,
        "end": window.window.end,
        "covered": window.covered,
        **_series_figures(window),
        "relative": {
            name: _figure(figure) for name, figure in risk_flows.relative_of(window).items()
        },
    }


def _series_figures(figures) -> dict:
    """The 12 §5.1-5.5 figures and the max drawdown's dates, of an ex-post window or ex-ante's
    constant-weight mix."""
    drawdown = figures.drawdown
    return {
        "peak_date": drawdown.peak_date,
        "trough_date": drawdown.trough_date,
        "recovery_date": drawdown.recovery_date,
        "max_days_under_water": drawdown.max_days_under_water,
        **{name: _figure(figure) for name, figure in risk_flows.figures_of(figures).items()},
    }


def _figure(figure: RiskFigure) -> dict:
    return {
        **asdict(figure),
        "frequency": str(figure.frequency),
        "flags": sorted(str(flag) for flag in figure.flags),
    }


def get_ex_ante_risk(
    *,
    assets: list[tuple[str, Decimal]],
    window: StandardWindow = StandardWindow.Y3,
    benchmark: str = DEFAULT_BENCHMARK,
    currency: str = risk_flows.EUR_RETURNS,
    exclude: Collection[str] = (),
    settings: Settings | None = None,
) -> dict:
    """Ex-ante risk of `assets` (ticker, weight) pairs held at constant weights, over `window`
    (see risk/flows.compute_ex_ante). Read-only and per request, like a backtest: no user data
    is read, so there is no database connection and no user_id."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = require_datastore_client(settings, command="ex-ante risk")
    logger.info("get-ex-ante-risk: measuring ex-ante risk of %d asset(s).", len(assets))
    result = risk_flows.compute_ex_ante(
        datastore_client,
        assets,
        window=window,
        benchmark=benchmark,
        currency=currency,
        exclude=exclude,
    )

    included = sum(1 for s in result.statuses if s.status == risk_flows.INCLUDED)
    if result.report is None:
        logger.warning(
            "get-ex-ante-risk: done, no asset could be included (%d requested), so there are no "
            "figures.",
            len(assets),
        )
    else:
        logger.info(
            "get-ex-ante-risk: done, %d of %d asset(s) included, window %s, risk-free rate %s, "
            "benchmark %s.",
            included,
            len(assets),
            window,
            "available" if result.rf_available else "unavailable",
            (
                result.benchmark.ticker
                if result.benchmark.available
                else f"{result.benchmark.ticker} unavailable ({result.benchmark.reason})"
            ),
        )
    payload = {
        "label": result.label,
        "currency": result.currency,
        "rf_available": result.rf_available,
        "benchmark": asdict(result.benchmark),
        "statuses": [asdict(status) for status in result.statuses],
    }
    report = result.report
    if report is None:
        return {
            **payload,
            **dict.fromkeys(
                (
                    "window",
                    "sigma_p",
                    "diversification_ratio",
                    "effective_n",
                    "holdings",
                    "constant_weight",
                    "correlation",
                )
            ),
        }
    decomposition = report.decomposition
    return {
        **payload,
        "window": {
            "label": str(report.requested.label),
            "requested_start": report.requested.start,
            "start": None if report.window is None else report.window.start,
            "end": report.requested.end,
            "limited_by": list(report.limited_by),
        },
        "sigma_p": _figure(decomposition.sigma_p),
        "diversification_ratio": _figure(decomposition.diversification_ratio),
        "effective_n": decomposition.effective_n,
        "holdings": [
            {
                "ticker": holding.key,
                "weight": holding.weight,
                "volatility": holding.volatility,
                "mcr": holding.mcr,
                "cr": holding.cr,
                "pcr": holding.pcr,
                "corr_with_portfolio": holding.corr_with_portfolio,
            }
            for holding in decomposition.holdings
        ],
        "constant_weight": _series_figures(report.constant_weight),
        "correlation": {
            "keys": list(report.correlation.keys),
            "benchmark_key": result.benchmark_key,
            "cells": [
                {"a": a, "b": b, "figure": _figure(figure)}
                for a, b, figure in risk_flows.correlation_cells(report)
            ],
        },
    }


def get_what_if(
    *,
    assets: list[tuple[str, Decimal]],
    candidate: str,
    weight: Decimal,
    funding: Funding,
    window: StandardWindow = StandardWindow.Y3,
    exclude: Collection[str] = (),
    settings: Settings | None = None,
) -> dict:
    """`assets` (ticker, weight) pairs before and after adding `candidate` at `weight` (a
    fraction of the whole mix) under `funding` (see risk/flows.compute_what_if). Read-only and
    per request, like ex-ante: no database connection and no user_id."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = require_datastore_client(settings, command="a what-if")
    logger.info(
        "get-what-if: comparing %d asset(s) before and after adding %s.", len(assets), candidate
    )
    result = risk_flows.compute_what_if(
        datastore_client,
        assets,
        candidate,
        float(weight),
        funding,
        window=window,
        exclude=exclude,
    )

    report = result.report
    included = sum(1 for s in result.statuses if s.status == risk_flows.INCLUDED)
    if report is None:
        if result.candidate.status != risk_flows.INCLUDED:
            why = f"the candidate {candidate}: {result.candidate.reason}"
        elif result.funding_reason is not None:
            why = result.funding_reason
        else:
            why = "no holding could be included"
        logger.warning(
            "get-what-if: done, nothing to compare: %s (%d of %d asset(s) included).",
            why,
            included,
            len(assets),
        )
    else:
        logger.info(
            "get-what-if: done, %d of %d asset(s) included, window %s, risk-free rate %s.",
            included,
            len(assets),
            window,
            "available" if result.rf_available else "unavailable",
        )

    payload = {
        "label": result.label,
        "rf_available": result.rf_available,
        "statuses": [asdict(status) for status in result.statuses],
        "candidate": {
            **asdict(result.candidate),
            "requested_weight": float(weight),
            "weight": None if report is None else report.funded.candidate_weight,
            "capped": None if report is None else report.funded.capped,
            "survivorship": result.survivorship,
        },
        "funding": {
            "rule": funding.rule,
            "ticker": funding.ticker,
            "reason": result.funding_reason,
        },
    }
    if report is None:
        return {**payload, **dict.fromkeys(("window", "weights", "rows", "threshold"))}
    return {**payload, **_what_if_sections(report, result.weights)}


def _what_if_sections(report: WhatIfReport, before: dict[str, float]) -> dict:
    after = report.after
    without = report.without_candidate
    threshold = report.threshold
    return {
        "window": {
            "label": str(after.requested.label),
            "requested_start": after.requested.start,
            "start": None if after.window is None else after.window.start,
            "end": after.requested.end,
            "limited_by": list(after.limited_by),
            "shortened": report.shortened,
            "start_without_candidate": None if without is None else without.start,
            "end_without_candidate": None if without is None else without.end,
        },
        "weights": {"before": before, "after": report.funded.after},
        "rows": {
            "sigma_p": _what_if_row(report.sigma_p),
            "diversification_ratio": _what_if_row(report.diversification_ratio),
            "candidate_pcr": _what_if_row(report.candidate_pcr),
            "candidate_correlation": _what_if_row(report.candidate_correlation),
            "sharpe": {**_what_if_row(report.sharpe), "delta_se": report.sharpe.delta_se},
            "max_drawdown": _what_if_row(report.max_drawdown),
        },
        "threshold": {
            **asdict(threshold),
            "candidate_sharpe": (
                None if threshold.candidate_sharpe is None else _figure(threshold.candidate_sharpe)
            ),
        },
    }


def _what_if_row(row: Row) -> dict:
    return {
        "before": None if row.before is None else _figure(row.before),
        "after": _figure(row.after),
        "delta": row.delta,
    }


def get_backtest(
    *,
    assets: list[tuple[str, Decimal]],
    period: Period,
    benchmark: str = DEFAULT_BENCHMARK,
    settings: Settings | None = None,
) -> dict:
    """A Labs backtest of `assets` (ticker, weight) pairs: every period's return for the mix and
    each asset, the growth series for `period`, and a status for every asset (see
    labs/flows.run_backtest). Read-only and per request: nothing is stored, and no user data is
    read, so there is no database connection and no user_id."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = require_datastore_client(settings, command="a backtest")
    logger.info(
        "backtest: %d asset(s), growth series for %s, against %s.", len(assets), period, benchmark
    )
    outcome = labs_flows.run_backtest(datastore_client, assets, period, benchmark=benchmark)

    _log_mix_left_out(outcome.result)
    included = sum(1 for s in outcome.statuses if s.status == labs_flows.INCLUDED)
    left_out = len(outcome.statuses) - included
    if included:
        logger.info(
            "backtest: done, %d included, %d left out, ending %s.",
            included,
            left_out,
            outcome.result.end_date,
        )
    else:
        logger.warning(
            "backtest: done, no asset could be included (%d left out), so there are no figures.",
            left_out,
        )
    result = asdict(outcome.result)
    del result["unavailable"]  # `statuses` covers every asset left out, with its reason
    risk = None if outcome.risk is None else _sorted_flags(asdict(outcome.risk))
    return {**result, "statuses": [asdict(s) for s in outcome.statuses], "risk": risk}


def _sorted_flags(value):
    """asdict keeps a RiskFigure's flags as a frozenset; the response lists them in a stable
    order."""
    if isinstance(value, dict):
        return {key: _sorted_flags(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sorted_flags(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(value)
    return value


def _log_mix_left_out(result: BacktestResult) -> None:
    """One line per included asset the mix left out of any period for not covering it."""
    if result.mix is None:
        return
    periods_by_ticker: dict[str, list[str]] = {}
    for period, figure in result.mix.periods.items():
        for ticker in figure.left_out:
            periods_by_ticker.setdefault(ticker, []).append(period)
    for ticker, periods in periods_by_ticker.items():
        logger.info(
            "backtest: the mix leaves out %s for %s: not enough history.",
            ticker,
            ", ".join(periods),
        )


# Every look-through weight is a share of the mix; 8 places is a millionth of a percent.
_LOOK_THROUGH_PLACES = Decimal("0.00000001")


def get_look_through(
    *, assets: list[tuple[str, Decimal]], settings: Settings | None = None
) -> dict:
    """The look-through exposures of `assets` (ticker, weight) pairs (see
    look_through/flows.look_through_mix). Read-only and per request, like ex-ante: no user data
    is read, so there is no database connection and no user_id."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = require_datastore_client(settings, command="look-through")
    run = look_through_flows.look_through_mix(datastore_client, assets)

    left_out = sum(1 for a in run.assets if a.status != look_through_flows.INCLUDED)
    exposures = run.exposures
    if exposures is None:
        logger.warning(
            "get-look-through: done, no asset could be included (%d requested), so there are "
            "no exposures.",
            len(assets),
        )
        return {"assets": [asdict(a) for a in run.assets], "exposures": None}

    coverage = exposures.coverage
    logger.info(
        "get-look-through: done: %s looked through, %s direct and matched in a fund, %s direct "
        "in no fund, %s commodity, %s unmapped; %d snapshot(s), %d name(s), %d asset(s) left "
        "out.",
        *(f"{share * 100:.1f}%" for share in coverage.values()),
        len(exposures.snapshots),
        exposures.names.n_names,
        left_out,
    )
    return {
        "assets": [{**asdict(a), "weight": _look_through_decimal(a.weight)} for a in run.assets],
        "exposures": {
            "snapshots": [
                {**asdict(s), "raw_weight_sum": _look_through_decimal(s.raw_weight_sum)}
                for s in exposures.snapshots
            ],
            "coverage": {k: _look_through_decimal(v) for k, v in coverage.items()},
            "dimensions": {
                key: _look_through_dimension(key, getattr(exposures, key))
                for key in DIMENSION_LABELS
            },
            "names": {
                "top": [
                    {
                        "issuer": n.issuer,
                        "location": n.location,
                        "weight": _look_through_decimal(n.weight),
                        "sources": list(n.sources),
                    }
                    for n in exposures.names.top
                ],
                "n_names": exposures.names.n_names,
                "concentration": _look_through_concentration(exposures.names.concentration),
            },
        },
    }


def _look_through_dimension(key: str, dimension: Dimension) -> dict:
    return {
        "label": DIMENSION_LABELS[key],
        "buckets": [
            {"label": b.label, "weight": _look_through_decimal(b.weight), "kind": b.kind}
            for b in dimension.buckets
        ],
        "concentration": _look_through_concentration(dimension.concentration),
    }


def _look_through_concentration(c: Concentration) -> dict:
    return {
        "top_shares": {n: _look_through_decimal(v) for n, v in c.top_shares.items()},
        "known_share": _look_through_decimal(c.known_share),
        "herfindahl": _look_through_decimal(c.herfindahl),
        "effective_n": _look_through_decimal(c.effective_n),
        "reason": c.reason,
    }


def _look_through_decimal(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_LOOK_THROUGH_PLACES)
