"""GET /risk (RK-12): how risky the user's portfolio has been, and whether the risk was worth it.

The page is built of sections, each loaded on its own and failing on its own, as /labs's
holdings and backtest do. A section that can't load shows its notice in its place; the others,
and the page around them, still render. Two so far, gathered concurrently, since neither needs
the other's answer:
- the ex-post summary: portfolio's GET /risk/ex-post, the standard windows' figures measured on
  the TWR of invested positions (RK-11);
- the ex-ante section (RK-18): portfolio's POST /risk/ex-ante on today's holdings at their
  weights. It takes two calls in order, as /labs's default mix does: GET /holdings
  (labs_view.build_default_mix), then the figures. `?ex_ante_window=` picks the window (3Y by
  default) and `?exclude=` (repeatable) leaves holdings out, typically the one that limits the
  window (R12).
Later sections (concentration, the what-if) add a loader each.

`?benchmark=` (RK-14) picks what the summary's relative figures measure against, portfolio's
VWCE.DE by default (benchmark_param.py has the rule). The field suggests the tracked tickers,
fetched alongside the figures; a failed list leaves the field usable, as on /labs.

On its own router, like labs.py, because routes.py is close to the repo's 64KB file limit; it
renders through routes.py's _render, so a cookie set earlier in the request still reaches the
response.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import _load_tickers, _render
from web.auth_client.base import AuthUser
from web.benchmark_param import chosen_benchmark
from web.labs_view import MAX_BACKTEST_ASSETS, build_default_mix
from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import PortfolioClientError
from web.risk_ex_ante_view import (
    ExAnteSection,
    build_ex_ante_section,
    chosen_ex_ante_window,
)
from web.risk_view import ExPostTable, build_ex_post_table
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

_UNAVAILABLE = "Can't reach the portfolio service right now, so it can't be shown."


async def _load_ex_post(
    portfolio_client: PortfolioClient, user_id: str, benchmark: str
) -> tuple[ExPostTable | None, str | None]:
    """The ex-post summary and None, or None and the message to show in its place. Both None:
    nothing to measure yet."""
    logger.info("risk: loading the ex-post figures against %s.", benchmark)
    try:
        risk = await portfolio_client.ex_post_risk(user_id=user_id, benchmark=benchmark)
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable loading the ex-post figures.")
        return None, _UNAVAILABLE
    table = build_ex_post_table(risk)
    if table is None:
        logger.info("risk: nothing to measure yet, no TWR returns.")
        return None, None
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
    return table, None


async def _load_ex_ante(
    portfolio_client: PortfolioClient,
    user_id: str,
    benchmark: str,
    window: str,
    exclude: list[str],
    params: list[tuple[str, str]],
) -> tuple[ExAnteSection | None, str | None]:
    """The ex-ante section and None, or None and the message to show in its place. Both None:
    no holding to measure yet."""
    try:
        holdings = await portfolio_client.list_holdings(user_id=user_id)
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable listing holdings for ex-ante.")
        return None, _UNAVAILABLE
    mix = build_default_mix(holdings.holdings)
    if not mix.assets:
        logger.info(
            "risk: ex-ante has nothing to measure, %d holdings, none priced and worth something.",
            len(holdings.holdings),
        )
        return None, None
    tickers = [ticker for ticker, _ in mix.assets]
    stale = [ticker for ticker in exclude if ticker not in tickers]
    if stale:
        # A link from before a sale, or a hand-edited URL: portfolio would 422 the whole mix.
        logger.info("risk: ex-ante exclusion(s) %s not in the mix, ignored.", ", ".join(stale))
    sent = list(dict.fromkeys(t for t in exclude if t in tickers))
    if set(tickers) <= set(sent):
        logger.info("risk: ex-ante exclusions would leave nothing, measuring the whole mix.")
        sent = []
    logger.info(
        "risk: loading the ex-ante figures for %d holdings over %s against %s, excluding %s; "
        "%d unpriced, %d worth nothing, %d over the %d cap.",
        len(mix.assets),
        window,
        benchmark,
        ", ".join(sent) or "none",
        len(mix.unpriced),
        len(mix.unweighted),
        len(mix.dropped),
        MAX_BACKTEST_ASSETS,
    )
    try:
        risk = await portfolio_client.ex_ante_risk(
            assets=list(mix.assets), window=window, benchmark=benchmark, exclude=sent
        )
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable loading the ex-ante figures.")
        return None, _UNAVAILABLE
    section = build_ex_ante_section(risk, mix, holdings.holdings, params)
    _log_ex_ante(risk, section)
    return section, None


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
        "%d of %d correlation cells greyed, %d outsized, %d hedges, excluded %s, left out %s.",
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
        ", ".join(section.excluded) or "none",
        left_out,
    )


@router.get("/risk")
async def risk(
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
    # The header's logout and theme forms need the token, as on every other page.
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    benchmark, benchmark_error = chosen_benchmark(benchmark)
    if benchmark_error:
        logger.info("risk: benchmark field over the length limit, measuring against %s.", benchmark)
    window = chosen_ex_ante_window(ex_ante_window)
    if ex_ante_window is not None and window != ex_ante_window:
        logger.info("risk: unknown ex-ante window %r, showing %s.", ex_ante_window, window)
    params = request.query_params.multi_items()
    (ex_post, ex_post_error), (ex_ante, ex_ante_error), (tickers, tickers_notice) = (
        await asyncio.gather(
            _load_ex_post(portfolio_client, user.user_id, benchmark),
            _load_ex_ante(portfolio_client, user.user_id, benchmark, window, exclude, params),
            _load_tickers(portfolio_client),
        )
    )
    return _render(
        request,
        response,
        templates,
        "risk.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "nav_active": "risk",
            "ex_post": ex_post,
            "ex_post_error": ex_post_error,
            "ex_ante": ex_ante,
            "ex_ante_error": ex_ante_error,
            "ex_ante_window": window,
            # The benchmark form re-sends these, so comparing changes nothing else (RK-14).
            "benchmark_kept": [(k, v) for k, v in params if k in ("ex_ante_window", "exclude")],
            "benchmark": benchmark,
            "benchmark_error": benchmark_error,
            "tickers": tickers,
            "tickers_notice": tickers_notice,
        },
    )
