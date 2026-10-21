"""GET /risk (RK-12): how risky the user's portfolio has been, and whether the risk was worth it.

The page is built of sections, each loaded on its own and failing on its own, as /labs's
holdings and backtest do. A section that can't load shows its notice in its place; the others,
and the page around them, still render. Today there is one, the ex-post summary: portfolio's
GET /risk/ex-post, the standard windows' figures measured on the TWR of invested positions
(RK-11). Later sections (ex-ante, concentration, the what-if) add a loader each, gathered
concurrently, since none needs another's answer.

On its own router, like labs.py, because routes.py is close to the repo's 64KB file limit; it
renders through routes.py's _render, so a cookie set earlier in the request still reaches the
response.
"""

import logging

from fastapi import APIRouter, Depends, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import _render
from web.auth_client.base import AuthUser
from web.portfolio_client.base import DEFAULT_BENCHMARK, PortfolioClient
from web.portfolio_client.errors import PortfolioClientError
from web.risk_view import ExPostTable, build_ex_post_table
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

_UNAVAILABLE = "Can't reach the portfolio service right now, so it can't be shown."


async def _load_ex_post(
    portfolio_client: PortfolioClient, user_id: str
) -> tuple[ExPostTable | None, str | None]:
    """The ex-post summary and None, or None and the message to show in its place. Both None:
    nothing to measure yet."""
    logger.info("risk: loading the ex-post figures.")
    try:
        risk = await portfolio_client.ex_post_risk(user_id=user_id, benchmark=DEFAULT_BENCHMARK)
    except PortfolioClientError:
        logger.exception("risk: portfolio service unavailable loading the ex-post figures.")
        return None, _UNAVAILABLE
    table = build_ex_post_table(risk)
    if table is None:
        logger.info("risk: nothing to measure yet, no TWR returns.")
        return None, None
    figures = [figure for window in risk.windows for figure in window.figures.values()]
    excluded = ", ".join(e.ticker for e in risk.excluded) or "none"
    logger.info(
        "risk: ex-post to %s, %d of %d windows covered, %d of %d figures greyed, "
        "risk-free rate %s, %d left out (%s).",
        risk.end_date,
        sum(1 for window in risk.windows if window.covered),
        len(risk.windows),
        sum(1 for figure in figures if figure.value is None),
        len(figures),
        "available" if risk.rf_available else "unavailable",
        len(risk.excluded),
        excluded,
    )
    return table, None


@router.get("/risk")
async def risk(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    # The header's logout and theme forms need the token, as on every other page.
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    ex_post, ex_post_error = await _load_ex_post(portfolio_client, user.user_id)
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
        },
    )
