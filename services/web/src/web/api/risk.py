"""The Risk pages (RK-12, split into sub-pages by UX-6): how risky the user's portfolio has
been, and whether the risk was worth it. Four routes under one sub-nav, each loading only its
own sections, each section loaded on its own and failing on its own, as /labs's holdings and
backtest do. A section that can't load shows its notice in its place; the page still renders.

- GET /risk, ex-post: portfolio's GET /risk/ex-post, the standard windows' figures measured on
  the TWR of invested positions (RK-11).
- GET /risk/ex-ante (RK-18): portfolio's POST /risk/ex-ante on today's holdings at their
  weights. `?ex_ante_window=` picks the window (3Y by default) and `?exclude=` (repeatable)
  leaves holdings out, typically the one that limits the window (R12).
- GET /risk/what-if: the what-if (RK-20), portfolio's POST /risk/what-if with the candidate,
  weight and funding from its GET form (`?candidate=`, `?candidate_weight=`, `?funding=`;
  risk_what_if_view.py checks them), and the candidate ranking (RK-29), POST
  /risk/what-if/ranking, every tracked ticker scored at the weight its GET form sends
  (`?rank_weight=`), in the order its links pick (`?rank_by=`; risk_ranking_view.py checks
  both). Both use ex-ante's window and exclusions, so "before" is the mix that page measures;
  the page names them and links there to change them. Only a sent form calls portfolio: the
  ranking costs about two data-store calls per tracked ticker.
- GET /risk/concentration (RK-23): portfolio's POST /risk/look-through on the same mix, always
  whole: `?exclude=` is ex-ante's, and concentration has no window.
Every page but ex-post measures today's mix, so each starts with GET /holdings
(labs_view.build_default_mix, `_load_mix`); a failed holdings call shows its notice in each
section of that page.

The sub-nav carries the whole query string, so every control's state survives a trip between
sub-pages, as it did when they were one page. Each GET form re-sends the others' fields for the
same reason.

The rolling charts (RK-25) come with ex-post's and ex-ante's own calls, so nothing more is
fetched and each section's charts fail with it: rolling 1Y volatility and Sharpe on both, and on
ex-ante the rolling correlation of the pair `?corr_a=&corr_b=` names (the heaviest holding
against the portfolio by default; risk_rolling_view.chosen_pair has the rule). So do the regime
tables (RK-28, risk_regime_view.py): each section's `regimes`, how its series and the benchmark
did in each market regime.

`?benchmark=` (RK-14) picks what ex-post's relative figures, and ex-ante's heatmap row, regimes
and pair, measure against: portfolio's VWCE.DE by default (benchmark_param.py has the rule). Both
pages have the field, which suggests the tracked tickers; a failed list leaves it usable.

The ex-ante window and Exclude/Include links, and the ranking's order, swap their own section
in place (UX-10): an htmx request aimed at #risk-ex-ante or #risk-ranking on its own page gets
that section alone (risk_ex_ante_swap.html, risk_what_if_swap.html), plus out-of-band copies of
what else carries the query, the sub-nav first. The route does the same work either way.

On its own router, like labs.py, because routes.py is close to the repo's 64KB file limit; it
renders through routes.py's _render, so a cookie set earlier in the request still reaches the
response.
"""

import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.htmx import swap_target, vary_on_swap
from web.api.routes import _load_tickers, _render
from web.auth_client.base import AuthUser
from web.benchmark_param import chosen_benchmark
from web.formatting import pct1
from web.labs_view import MAX_BACKTEST_ASSETS, DefaultMix, build_default_mix
from web.portfolio_client.base import Holding, LookThrough, PortfolioClient
from web.portfolio_client.errors import PortfolioClientError
from web.risk_concentration_view import (
    ConcentrationSection,
    build_concentration_section,
)
from web.risk_ex_ante_view import (
    ExAnteSection,
    build_ex_ante_section,
    chosen_ex_ante_window,
)
from web.risk_ranking_view import (
    RankingForm,
    RankingSection,
    build_ranking_section,
    parse_ranking,
)
from web.risk_regime_view import RegimeSection, build_regime_section
from web.risk_rolling_view import (
    PAIR_PARAMS,
    CorrelationChart,
    RollingCharts,
    RollingSeries,
    build_correlation_chart,
    build_rolling_charts,
    chosen_pair,
)
from web.risk_view import ExPostTable, build_ex_post_table
from web.risk_what_if_view import (
    WhatIfForm,
    WhatIfSection,
    build_what_if_section,
    parse_what_if,
)
from web.settings import Settings
from web.ticker_names import load_names

logger = logging.getLogger(__name__)

router = APIRouter()

# The query each GET form re-sends as hidden inputs: every other control's (RK-14, RK-20,
# RK-25's rolling pair and RK-29's ranking).
_RANKING_PARAMS = ("rank_weight", "rank_by")
_BENCHMARK_KEPT = (
    "candidate",
    "candidate_weight",
    "funding",
    "ex_ante_window",
    "exclude",
    *PAIR_PARAMS,
    *_RANKING_PARAMS,
)
_WHAT_IF_KEPT = ("benchmark", "ex_ante_window", "exclude", *PAIR_PARAMS, *_RANKING_PARAMS)
# The ranking's own weight is its field; its order is kept, so a new weight keeps the order.
_RANKING_KEPT = (
    "benchmark",
    "ex_ante_window",
    "exclude",
    *PAIR_PARAMS,
    "candidate",
    "candidate_weight",
    "funding",
    "rank_by",
)

_UNAVAILABLE = "Can't reach the portfolio service right now, so it can't be shown."


@dataclass(frozen=True)
class _Extras:
    """What a section shows under its figures: its rolling charts (RK-25) and its regime table
    (RK-28). None where portfolio had nothing to measure."""

    charts: RollingCharts | None = None
    correlation: CorrelationChart | None = None
    regimes: RegimeSection | None = None


def _n_points(series: RollingSeries) -> int:
    """Every point, greyed ones included: a series greyed throughout has no payload."""
    return len(series.payload["dates"]) if series.payload else sum(g.count for g in series.gaps)


def _log_rolling(section: str, charts: RollingCharts | None) -> None:
    if charts is None:
        return
    if not charts.series:
        logger.info("risk: %s rolling: no point, the history is under a year.", section)
        return
    logger.info(
        "risk: %s rolling 1Y: %d monthly point(s), %s.",
        section,
        _n_points(charts.series[0]),
        ", ".join(
            f"{sum(g.count for g in s.gaps)} {s.key} greyed" + ("" if s.payload else " (all)")
            for s in charts.series
        ),
    )


async def _load_ex_post(
    portfolio_client: PortfolioClient, user_id: str, benchmark: str
) -> tuple[ExPostTable | None, str | None, _Extras]:
    """The ex-post summary and None, or None and the message to show in its place, then its
    rolling charts and regime table. Both None: nothing to measure yet."""
    logger.info("risk: loading the ex-post figures against %s.", benchmark)
    try:
        risk = await portfolio_client.ex_post_risk(user_id=user_id, benchmark=benchmark)
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable loading the ex-post figures.")
        return None, _UNAVAILABLE, _Extras()
    table = build_ex_post_table(risk)
    if table is None:
        logger.info("risk: nothing to measure yet, no TWR returns.")
        return None, None, _Extras()
    figures = [figure for window in risk.windows for figure in window.figures.values()]
    relative = [figure for window in risk.windows for figure in window.relative.values()]
    excluded = ", ".join(e.ticker for e in risk.excluded) or "none"
    bench = risk.benchmark
    logger.info(
        "risk: ex-post to %s, %d of %d windows covered, %d of %d figures greyed, "
        "risk-free rate %s, against %s (%s), %d of %d relative figures greyed, %d left out (%s).",
        risk.end_date,
        sum(1 for window in risk.windows if window.covered),
        len(risk.windows),
        sum(1 for figure in figures if figure.value is None),
        len(figures),
        "available" if risk.rf_available else "unavailable",
        bench.ticker,
        "available" if bench.available else f"unavailable: {bench.reason}",
        sum(1 for figure in relative if figure.value is None),
        len(relative),
        len(risk.excluded),
        excluded,
    )
    rolling = build_rolling_charts(risk.rolling, "ex-post")
    _log_rolling("ex-post", rolling)
    regimes = build_regime_section(risk.regimes, risk.label, risk.benchmark)
    _log_regimes("ex-post", regimes)
    return table, None, _Extras(charts=rolling, regimes=regimes)


@dataclass(frozen=True)
class _WhatIfParts:
    """The what-if section's form, and the section or the message to show in its place. No
    form: there's no mix to compare against (no holding yet, or holdings couldn't load)."""

    form: WhatIfForm | None = None
    section: WhatIfSection | None = None
    error: str | None = None


@dataclass(frozen=True)
class _WhatIfAsk:
    candidate: str | None
    weight: str | None
    funding: str | None


@dataclass(frozen=True)
class _RankingParts:
    """The ranking's form, and the section or the message to show in its place. No form, as for
    the what-if: there's no mix to rank against."""

    form: RankingForm | None = None
    section: RankingSection | None = None
    error: str | None = None


@dataclass(frozen=True)
class _RankingAsk:
    weight: str | None
    rank_by: str | None


@dataclass(frozen=True)
class _Mix:
    """Today's mix and its holdings, which every sub-page but ex-post measures. No mix: no
    holding to measure yet, or, with `error`, the holdings couldn't load."""

    mix: DefaultMix | None = None
    holdings: tuple[Holding, ...] = ()
    error: str | None = None


async def _load_mix(portfolio_client: PortfolioClient, user_id: str, page: str) -> _Mix:
    try:
        holdings = await portfolio_client.list_holdings(user_id=user_id)
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable listing holdings.")
        return _Mix(error=_UNAVAILABLE)
    mix = build_default_mix(holdings.holdings)
    if not mix.assets:
        logger.info(
            "risk: %s has nothing to measure, %d holdings, none priced and worth something.",
            page,
            len(holdings.holdings),
        )
        return _Mix()
    return _Mix(mix, holdings.holdings)


def _sent_exclusions(mix: DefaultMix, exclude: list[str]) -> list[str]:
    """`?exclude=` as ex-ante and the what-if send it: in the mix, once each, never all of it."""
    tickers = [ticker for ticker, _ in mix.assets]
    stale = [ticker for ticker in exclude if ticker not in tickers]
    if stale:
        # A link from before a sale, or a hand-edited URL: portfolio would 422 the whole mix.
        logger.info("risk: ex-ante exclusion(s) %s not in the mix, ignored.", ", ".join(stale))
    sent = list(dict.fromkeys(t for t in exclude if t in tickers))
    if set(tickers) <= set(sent):
        logger.info("risk: ex-ante exclusions would leave nothing, measuring the whole mix.")
        sent = []
    return sent


async def _load_ex_ante(
    portfolio_client: PortfolioClient,
    mix: DefaultMix,
    holdings: tuple[Holding, ...],
    benchmark: str,
    window: str,
    sent: list[str],
    params: list[tuple[str, str]],
) -> tuple[ExAnteSection | None, str | None, _Extras]:
    """The ex-ante section and None, or None and the message to show in its place, then its
    rolling charts and regime table."""
    measured = [ticker for ticker, _ in mix.assets if ticker not in sent]
    raw = dict((k, v) for k, v in params if k in PAIR_PARAMS)
    pair = chosen_pair(raw.get("corr_a"), raw.get("corr_b"), measured, benchmark)
    if pair.dropped:
        # A link from before a sale or an exclusion, or a hand-edited URL: portfolio would 422.
        logger.info("risk: rolling pair dropped (%s), showing the default.", pair.dropped)
    logger.info(
        "risk: loading the ex-ante figures for %d holdings over %s against %s, excluding %s, "
        "rolling pair %s vs %s (%s); %d unpriced, %d worth nothing, %d over the %d cap.",
        len(mix.assets),
        window,
        benchmark,
        ", ".join(sent) or "none",
        pair.a,
        pair.b or "the portfolio",
        "requested" if pair.requested else "default",
        len(mix.unpriced),
        len(mix.unweighted),
        len(mix.dropped),
        MAX_BACKTEST_ASSETS,
    )
    try:
        risk = await portfolio_client.ex_ante_risk(
            assets=list(mix.assets),
            window=window,
            benchmark=benchmark,
            exclude=sent,
            rolling_pair=(pair.a, pair.b),
        )
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable loading the ex-ante figures.")
        return None, _UNAVAILABLE, _Extras()
    section = build_ex_ante_section(risk, mix, holdings, params)
    _log_ex_ante(risk, section)
    extras = _Extras(
        charts=build_rolling_charts(risk.rolling, "ex-ante"),
        correlation=build_correlation_chart(
            risk.rolling_correlation, pair, measured, benchmark, params
        ),
        regimes=build_regime_section(risk.regimes, risk.label, risk.benchmark),
    )
    _log_rolling("ex-ante", extras.charts)
    _log_rolling_pair(extras.correlation)
    _log_regimes("ex-ante", extras.regimes)
    return section, None, extras


def _log_regimes(section: str, regimes: RegimeSection | None) -> None:
    if regimes is None:
        return
    if not regimes.groups:
        logger.info("risk: %s regimes: none shown, %s.", section, regimes.reason)
        return
    lines = [line for group in regimes.groups for line in group.lines]
    logger.info(
        "risk: %s regimes: %d shown in %d group(s), benchmark greyed on %d.",
        section,
        len(lines),
        len(regimes.groups),
        sum(1 for line in lines if line.cells[3].value is None),
    )


def _log_rolling_pair(chart: CorrelationChart | None) -> None:
    if chart is None:
        return
    if chart.series is None:
        logger.info(
            "risk: rolling correlation %s vs %s: no point, %s.",
            chart.a,
            chart.b_label,
            chart.reason,
        )
        return
    logger.info(
        "risk: rolling correlation %s vs %s: %d weekly point(s), %d greyed.",
        chart.a,
        chart.b_label,
        _n_points(chart.series),
        sum(g.count for g in chart.series.gaps),
    )


async def _load_what_if(
    portfolio_client: PortfolioClient,
    mix: DefaultMix,
    window: str,
    sent: list[str],
    form: WhatIfForm,
) -> _WhatIfParts:
    request = form.request
    if request is None:
        if form.errors:
            logger.info("risk: what-if form has errors (%s), nothing sent.", ", ".join(form.errors))
        else:
            logger.info("risk: what-if form empty, nothing sent.")
        return _WhatIfParts(form=form)
    logger.info(
        "risk: loading the what-if for %s at %s%% of the mix, funding %s, over %s, %d holdings, "
        "excluding %s.",
        request.ticker,
        f"{(request.weight * 100).normalize():f}",
        request.funding,
        window,
        len(mix.assets),
        ", ".join(sent) or "none",
    )
    try:
        what_if = await portfolio_client.what_if(
            assets=list(mix.assets),
            candidate=(request.ticker, request.weight),
            funding=request.funding,
            window=window,
            exclude=sent,
        )
    except PortfolioClientError:
        # A 422 included: the form checks what portfolio rejects, so one means they disagree.
        logger.exception("risk: portfolio service unavailable loading the what-if.")
        return _WhatIfParts(form=form, error=_UNAVAILABLE)
    section = build_what_if_section(what_if)
    _log_what_if(section)
    return _WhatIfParts(form=form, section=section)


def _log_what_if(section: WhatIfSection) -> None:
    if section.nothing:
        logger.info("risk: what-if had nothing to compare: %s", section.nothing)
        return
    window, candidate, threshold = section.window, section.candidate, section.threshold
    cells = [c for line in section.lines for c in (line.before, line.after) if c is not None]
    if threshold is None or not threshold.applicable:
        verdict = "not shown"
    elif threshold.improves is None:
        verdict = f"greyed ({threshold.reason})"
    else:
        verdict = "met" if threshold.improves else "not met"
    left_out = ", ".join(f"{h.ticker} ({h.reason})" for h in section.left_out) or "none"
    logger.info(
        "risk: what-if %s over %s, %s to %s, %s, %s, survivorship %s, %d of %d figures greyed, "
        "threshold %s, left out %s.",
        candidate.ticker,
        window.label,
        window.start or "no overlap",
        window.end,
        f"shortened by {', '.join(window.limited_by)}" if window.shortened else "not shortened",
        f"capped at {pct1(candidate.weight)}" if candidate.capped else "not capped",
        "flagged" if section.survivorship else "not flagged",
        sum(1 for cell in cells if cell.value is None),
        len(cells),
        verdict,
        left_out,
    )


async def _load_ranking(
    portfolio_client: PortfolioClient,
    mix: DefaultMix,
    window: str,
    sent: list[str],
    form: RankingForm,
    params: list[tuple[str, str]],
) -> _RankingParts:
    request = form.request
    if request is None:
        if form.error:
            logger.info("risk: ranking form has an error (%s), nothing sent.", form.error)
        elif form.window_note:
            logger.info("risk: ranking not sent over %s: it needs 3Y or more.", window)
        else:
            logger.info("risk: ranking not asked for, nothing sent.")
        return _RankingParts(form=form)
    logger.info(
        "risk: loading the ranking at %s%% of the mix, ranked by %s, over %s, %d holdings, "
        "excluding %s.",
        f"{(request.weight * 100).normalize():f}",
        request.rank_by,
        window,
        len(mix.assets),
        ", ".join(sent) or "none",
    )
    try:
        ranking = await portfolio_client.rank_candidates(
            assets=list(mix.assets),
            weight=request.weight,
            window=window,
            exclude=sent,
            rank_by=request.rank_by,
        )
    except PortfolioClientError:
        # A 422 included: the form checks what portfolio rejects, so one means they disagree.
        logger.exception("risk: portfolio service unavailable loading the ranking.")
        return _RankingParts(form=form, error=_UNAVAILABLE)
    section = build_ranking_section(ranking, params)
    _log_ranking(section)
    return _RankingParts(form=form, section=section)


def _log_ranking(section: RankingSection) -> None:
    if section.nothing:
        logger.info("risk: ranking had nothing to rank: %s", section.nothing)
        return
    logger.info(
        "risk: ranking over %s, %d ranked, %d greyed, %d not ranked, %d past the cap of %d, "
        "first %s.",
        section.window.label,
        len(section.lines),
        len(section.greyed),
        len(section.unranked),
        len(section.over_cap),
        section.cap,
        section.lines[0].ticker if section.lines else "none",
    )


async def _load_concentration(
    portfolio_client: PortfolioClient, mix: DefaultMix
) -> tuple[ConcentrationSection | None, str | None]:
    """The concentration section and None, or None and the message to show in its place."""
    logger.info("risk: loading the look-through for %d holdings.", len(mix.assets))
    try:
        lt = await portfolio_client.look_through(assets=list(mix.assets))
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable loading the look-through.")
        return None, _UNAVAILABLE
    section = build_concentration_section(lt, mix)
    _log_concentration(lt, section)
    return section, None


def _log_concentration(lt: LookThrough, section: ConcentrationSection) -> None:
    left_out = ", ".join(f"{h.ticker} ({h.reason})" for h in section.left_out) or "none"
    exposures = lt.exposures
    if exposures is None:
        logger.info("risk: look-through included nothing, left out: %s.", left_out)
        return
    share = exposures.coverage
    logger.info(
        "risk: look-through: %s looked through, %s direct (%s matched), %s commodity, "
        "%s unmapped (%s), snapshots %s, %d names, left out %s.",
        pct1(share["look_through"]),
        pct1(share["direct_matched"] + share["direct_unmatched"]),
        pct1(share["direct_matched"]),
        pct1(share["commodity"]),
        pct1(share["unmapped"]),
        ", ".join(fund.ticker for fund in section.unmapped) or "none",
        ", ".join(f"{s.ticker} {s.as_of_date.isoformat()}" for s in exposures.snapshots) or "none",
        exposures.names.n_names,
        left_out,
    )


def _log_ex_ante(risk, section: ExAnteSection) -> None:
    left_out = ", ".join(f"{h.ticker} ({h.reason})" for h in section.left_out) or "none"
    if section.window is None:
        logger.info("risk: ex-ante measured nothing, every holding left out: %s.", left_out)
        return
    figures = [cell for row in section.summary_rows for cell in row.cells]
    cells = risk.correlation.cells if risk.correlation else ()
    window = section.window
    logger.info(
        "risk: ex-ante over %s, %s to %s%s, %d of %d figures greyed, decomposition %s, "
        "%d of %d correlation cells greyed, %d outsized, %d hedges, FX decomposition %s, "
        "excluded %s, left out %s.",
        window.label,
        window.start or "no overlap",
        window.end,
        f", limited by {', '.join(window.limited_by)}" if window.limited_by else "",
        sum(1 for cell in figures if cell.value is None),
        len(figures),
        (
            "measured"
            if section.decomposition and section.decomposition.reason is None
            else "greyed"
        ),
        sum(1 for cell in cells if cell.figure.value is None),
        len(cells),
        len(section.decomposition.outsized) if section.decomposition else 0,
        len(section.decomposition.hedges) if section.decomposition else 0,
        _fx_summary(section),
        ", ".join(section.excluded) or "none",
        left_out,
    )


def _fx_summary(section: ExAnteSection) -> str:
    rows = section.fx_decomposition.rows if section.fx_decomposition else ()
    if not rows:
        return "none, every holding in EUR"
    cells = [cell for row in rows for cell in row.cells]
    greyed = sum(1 for cell in cells if cell.value is None)
    return f"{len(rows)} holdings, {greyed} of {len(cells)} figures greyed"


# The sub-nav (UX-6): each sub-page's key, path and label, in reading order.
_SUBPAGES = (
    ("ex-post", "/risk", "Ex-post"),
    ("ex-ante", "/risk/ex-ante", "Ex-ante"),
    ("what-if", "/risk/what-if", "What if"),
    ("concentration", "/risk/concentration", "Concentration"),
)


def _with_query(path: str, request: Request) -> str:
    """`path` with this request's query as it was sent: the sub-nav carries every control's
    state, so a trip to another sub-page and back changes nothing."""
    query = request.url.query
    return f"{path}?{query}" if query else path


@dataclass(frozen=True)
class _Page:
    """What every sub-page works out before loading anything."""

    benchmark: str
    window: str
    params: list[tuple[str, str]]
    context: dict


def _page(
    request: Request,
    response: Response,
    settings: Settings,
    user: AuthUser,
    current: str,
    benchmark: str | None = None,
    ex_ante_window: str | None = None,
) -> _Page:
    # The header's logout and theme forms need the token, as on every other page.
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    chosen, benchmark_error = chosen_benchmark(benchmark)
    if benchmark_error:
        logger.info("risk: benchmark field over the length limit, measuring against %s.", chosen)
    window = chosen_ex_ante_window(ex_ante_window)
    if ex_ante_window is not None and window != ex_ante_window:
        logger.info("risk: unknown ex-ante window %r, showing %s.", ex_ante_window, window)
    params = request.query_params.multi_items()
    context = {
        "user": user,
        "csrf_token": csrf_token,
        "nav_active": "risk",
        "risk_nav": [(key, _with_query(path, request), label) for key, path, label in _SUBPAGES],
        "risk_nav_active": current,
        "benchmark": chosen,
        "benchmark_error": benchmark_error,
        "ex_ante_window": window,
        # Each form re-sends the others' fields, so submitting one changes nothing else.
        "benchmark_kept": [(k, v) for k, v in params if k in _BENCHMARK_KEPT],
        "what_if_kept": [(k, v) for k, v in params if k in _WHAT_IF_KEPT],
        "ranking_kept": [(k, v) for k, v in params if k in _RANKING_KEPT],
    }
    return _Page(chosen, window, params, context)


@router.get("/risk")
async def risk(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
    benchmark: str | None = None,
):
    """Ex-post: how the invested holdings have actually behaved."""
    page = _page(request, response, settings, user, "ex-post", benchmark)
    (ex_post, ex_post_error, extras), (tickers, tickers_notice), ticker_names = (
        await asyncio.gather(
            _load_ex_post(portfolio_client, user.user_id, page.benchmark),
            _load_tickers(portfolio_client),
            load_names(portfolio_client),
        )
    )
    return _render(
        request,
        response,
        templates,
        "risk.html",
        {
            **page.context,
            "ex_post": ex_post,
            "ex_post_error": ex_post_error,
            "ex_post_rolling": extras,
            "ex_post_regimes": extras.regimes,
            "tickers": tickers,
            "tickers_notice": tickers_notice,
            "ticker_names": ticker_names,
        },
    )


async def _ex_ante_with_mix(
    portfolio_client: PortfolioClient,
    user_id: str,
    benchmark: str,
    window: str,
    exclude: list[str],
    params: list[tuple[str, str]],
) -> tuple[ExAnteSection | None, str | None, _Extras]:
    loaded = await _load_mix(portfolio_client, user_id, "ex-ante")
    if loaded.mix is None:
        return None, loaded.error, _Extras()
    sent = _sent_exclusions(loaded.mix, exclude)
    return await _load_ex_ante(
        portfolio_client, loaded.mix, loaded.holdings, benchmark, window, sent, params
    )


@router.get("/risk/ex-ante")
async def risk_ex_ante(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
    benchmark: str | None = None,
    ex_ante_window: str | None = None,
    exclude: list[str] = Query(default=[]),
):
    """Ex-ante: today's mix held at constant weights through one window."""
    page = _page(request, response, settings, user, "ex-ante", benchmark, ex_ante_window)
    (ex_ante, ex_ante_error, extras), (tickers, tickers_notice), ticker_names = (
        await asyncio.gather(
            _ex_ante_with_mix(
                portfolio_client, user.user_id, page.benchmark, page.window, exclude, page.params
            ),
            _load_tickers(portfolio_client),
            load_names(portfolio_client),
        )
    )
    target = swap_target(request, ("risk-ex-ante",))
    if target is not None:
        logger.info("risk: sending #%s only.", target)
    rendered = _render(
        request,
        response,
        templates,
        "risk_ex_ante.html" if target is None else "risk_ex_ante_swap.html",
        {
            **page.context,
            "ex_ante": ex_ante,
            "ex_ante_error": ex_ante_error,
            "ex_ante_rolling": extras,
            "ex_ante_regimes": extras.regimes,
            "tickers": tickers,
            "tickers_notice": tickers_notice,
            "ticker_names": ticker_names,
        },
    )
    return vary_on_swap(rendered)


async def _what_if_with_mix(
    portfolio_client: PortfolioClient,
    user_id: str,
    window: str,
    exclude: list[str],
    params: list[tuple[str, str]],
    ask: _WhatIfAsk,
    rank_ask: _RankingAsk,
) -> tuple[_WhatIfParts, _RankingParts, list[str] | None]:
    """The what-if's and the ranking's parts, then the exclusions both sent (None: no mix)."""
    loaded = await _load_mix(portfolio_client, user_id, "what-if and ranking")
    if loaded.mix is None:
        return _WhatIfParts(error=loaded.error), _RankingParts(error=loaded.error), None
    sent = _sent_exclusions(loaded.mix, exclude)
    form = parse_what_if(ask.candidate, ask.weight, ask.funding, loaded.mix, sent)
    rank_form = parse_ranking(rank_ask.weight, rank_ask.rank_by, window)
    what_if, ranking = await asyncio.gather(
        _load_what_if(portfolio_client, loaded.mix, window, sent, form),
        _load_ranking(portfolio_client, loaded.mix, window, sent, rank_form, params),
    )
    return what_if, ranking, sent


@router.get("/risk/what-if")
async def risk_what_if(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
    ex_ante_window: str | None = None,
    exclude: list[str] = Query(default=[]),
    candidate: str | None = None,
    candidate_weight: str | None = None,
    funding: str | None = None,
    rank_weight: str | None = None,
    rank_by: str | None = None,
):
    """What if: a candidate added to today's mix, and every tracked ticker ranked as one, over
    ex-ante's window and exclusions."""
    page = _page(request, response, settings, user, "what-if", None, ex_ante_window)
    (what_if, ranking, sent), (tickers, tickers_notice), ticker_names = await asyncio.gather(
        _what_if_with_mix(
            portfolio_client,
            user.user_id,
            page.window,
            exclude,
            page.params,
            _WhatIfAsk(candidate, candidate_weight, funding),
            _RankingAsk(rank_weight, rank_by),
        ),
        _load_tickers(portfolio_client),
        load_names(portfolio_client),
    )
    target = swap_target(request, ("risk-ranking",))
    if target is not None:
        logger.info("risk: sending #%s only.", target)
    rendered = _render(
        request,
        response,
        templates,
        "risk_what_if.html" if target is None else "risk_what_if_swap.html",
        {
            **page.context,
            "excluded": sent,
            "ex_ante_link": _with_query("/risk/ex-ante", request) + "#risk-ex-ante",
            "what_if_form": what_if.form,
            "what_if": what_if.section,
            "what_if_error": what_if.error,
            "ranking_form": ranking.form,
            "ranking": ranking.section,
            "ranking_error": ranking.error,
            "tickers": tickers,
            "tickers_notice": tickers_notice,
            "ticker_names": ticker_names,
        },
    )
    return vary_on_swap(rendered)


async def _concentration_with_mix(
    portfolio_client: PortfolioClient, user_id: str
) -> tuple[ConcentrationSection | None, str | None]:
    loaded = await _load_mix(portfolio_client, user_id, "look-through")
    if loaded.mix is None:
        return None, loaded.error
    return await _load_concentration(portfolio_client, loaded.mix)


@router.get("/risk/concentration")
async def risk_concentration(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """Concentration: today's whole mix, looked through its funds. No window, no exclusions."""
    page = _page(request, response, settings, user, "concentration")
    (concentration, concentration_error), ticker_names = await asyncio.gather(
        _concentration_with_mix(portfolio_client, user.user_id),
        load_names(portfolio_client),
    )
    return _render(
        request,
        response,
        templates,
        "risk_concentration.html",
        {
            **page.context,
            "concentration": concentration,
            "concentration_error": concentration_error,
            "ticker_names": ticker_names,
        },
    )
