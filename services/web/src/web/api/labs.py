"""GET /labs (LB-6): the user's current holdings backtested over 1M to Max.

Two calls, in order: GET /holdings for the default mix (labs_view.build_default_mix), then
portfolio's POST /labs/backtest for it. The second needs the first's answer, so they can't run
concurrently. Each fails on its own: a failed holdings call shows its notice and runs no
backtest, and a failed backtest still shows which holdings the mix left out.

The backtest asks for 1Y's growth series, which this page doesn't draw yet: the period only
picks the series, and 1Y keeps the response well under Max's (about 460 KB for 50 assets,
LB-5). The growth chart (LB-8) makes it a parameter.

On its own router, like portfolio.py, because routes.py is close to the repo's 64KB file limit;
it renders through routes.py's _render, so a cookie set earlier in the request still reaches
the response.
"""

import logging

from fastapi import APIRouter, Depends, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import _render
from web.auth_client.base import AuthUser
from web.labs_view import MAX_BACKTEST_ASSETS, DefaultMix, build_default_mix
from web.portfolio_client.base import BACKTEST_PERIODS, Backtest, PortfolioClient
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioClientError
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

_UNAVAILABLE = "Can't reach the portfolio service right now, so it can't be shown."
_GROWTH_PERIOD = "1Y"


async def _load_mix(portfolio_client: PortfolioClient, user_id: str) -> DefaultMix | None:
    """The default mix, or None if the holdings couldn't be loaded."""
    try:
        holdings = await portfolio_client.list_holdings(user_id=user_id)
    except PortfolioClientError:
        logger.exception("labs: portfolio service unavailable listing holdings.")
        return None
    mix = build_default_mix(holdings.holdings)
    logger.info(
        "labs: %d holdings, %d in the mix, %d unpriced, %d worth nothing, %d over the %d cap.",
        len(holdings.holdings),
        len(mix.assets),
        len(mix.unpriced),
        len(mix.unweighted),
        len(mix.dropped),
        MAX_BACKTEST_ASSETS,
    )
    return mix


async def _run_backtest(
    portfolio_client: PortfolioClient, mix: DefaultMix
) -> tuple[Backtest | None, str | None]:
    """The backtest and None, or None and the message to show in its place."""
    try:
        result = await portfolio_client.backtest(assets=list(mix.assets), period=_GROWTH_PERIOD)
    except InvalidPortfolioRequest as exc:
        # portfolio rejected the mix; its message is written to be shown.
        logger.warning("labs: portfolio rejected the mix: %s", exc)
        return None, str(exc)
    except PortfolioClientError:
        logger.exception("labs: portfolio service unavailable running the backtest.")
        return None, _UNAVAILABLE
    included = sum(1 for s in result.statuses if s.status == "included")
    logger.info(
        "labs: backtest to %s, %d included, %d left out.",
        result.end_date,
        included,
        len(result.statuses) - included,
    )
    return result, None


@router.get("/labs")
async def labs(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    # The header's logout and theme forms need the token, as on every other page.
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)

    mix = await _load_mix(portfolio_client, user.user_id)
    backtest, backtest_error = None, None
    if mix is not None and mix.assets:
        backtest, backtest_error = await _run_backtest(portfolio_client, mix)
    elif mix is not None:
        logger.info("labs: nothing to backtest, no holding has a market value.")
    return _render(
        request,
        response,
        templates,
        "labs.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "nav_active": "labs",
            "holdings_error": _UNAVAILABLE if mix is None else None,
            "mix": mix,
            "max_assets": MAX_BACKTEST_ASSETS,
            "backtest": backtest,
            "backtest_error": backtest_error,
            "periods": BACKTEST_PERIODS,
            "unavailable": (
                [] if backtest is None else [s for s in backtest.statuses if s.status != "included"]
            ),
        },
    )
