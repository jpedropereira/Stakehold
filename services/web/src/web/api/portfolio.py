"""GET /portfolio: every holding summed across the user's accounts (PF-4), and the old address
of one account's holdings page, kept working.

PF-2 moved the per-account page to /accounts/holdings (api/holdings.py). /portfolio?account=X
still answers a permanent redirect (308) there: a browser may cache it, but it caches per URL,
query included, so the bare /portfolio is unaffected. The redirect asks nobody who is logged in:
it reveals nothing, and the page it points at requires the login. So the handler takes no
require_user dependency, which would run before it can branch, and checks the login itself only
for the combined page. The query string is passed on as it arrived, so an encoded label stays
encoded, and the target path is fixed, so it can't send the visitor off the site.

The combined page sums nothing itself: portfolio's GET /holdings (PF-1) does, so this is one
call however many accounts there are. Its chart (PF-6) comes from one more,
GET /returns/portfolio (PF-5), so if that fails only the chart goes.

On its own router, like overview.py, because routes.py is close to the repo's 64KB file limit;
it renders through routes.py's _render, so a cookie set earlier in the request still reaches
the response.
"""

import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import (
    get_auth_client,
    get_portfolio_client,
    get_settings,
    get_templates,
    require_user,
)
from web.api.routes import _render
from web.auth_client.base import AuthClient
from web.portfolio_client.base import (
    Account,
    Holdings,
    PortfolioClient,
    PortfolioReturns,
    UnmappedInstrument,
)
from web.portfolio_client.errors import PortfolioClientError
from web.portfolio_view import (
    build_portfolio_chart_payload,
    build_portfolio_holdings,
    unmapped_across_brokers,
)
from web.settings import Settings
from web.ticker_status import holding_price_note, load_statuses

logger = logging.getLogger(__name__)

router = APIRouter()

_INCOMPLETE = "Can't reach the portfolio service right now — this page may be incomplete."
# Under its card's own heading, so it serves both the holdings card and the chart card.
_UNAVAILABLE = "Can't reach the portfolio service right now, so they can't be shown."


@dataclass(frozen=True)
class _PortfolioViewData:
    # None where that call failed, so the page never reads a failure as "none yet".
    accounts: list[Account] | None
    holdings: Holdings | None
    unmapped: list[UnmappedInstrument] | None
    returns: PortfolioReturns | None


async def _load_portfolio_view(
    portfolio_client: PortfolioClient, user_id: str
) -> _PortfolioViewData:
    """The user's accounts, holdings, unmapped instruments and return series, concurrently, each
    degrading on its own.

    The account list is fetched only to tell a user with no accounts (the first-run card) from
    one whose accounts hold nothing yet: /holdings answers both with an empty list.
    """
    calls = {
        "accounts": portfolio_client.list_accounts(user_id=user_id),
        "holdings": portfolio_client.list_holdings(user_id=user_id),
        "unmapped instruments": portfolio_client.list_unmapped_instruments(user_id=user_id),
        "returns": portfolio_client.list_portfolio_returns(user_id=user_id),
    }
    results = dict(
        zip(calls, await asyncio.gather(*calls.values(), return_exceptions=True), strict=True)
    )
    for name, result in results.items():
        if isinstance(result, PortfolioClientError):
            logger.exception("portfolio service unavailable listing %s.", name, exc_info=result)
            results[name] = None
        elif isinstance(result, BaseException):
            raise result
    return _PortfolioViewData(
        accounts=results["accounts"],
        holdings=results["holdings"],
        unmapped=results["unmapped instruments"],
        returns=results["returns"],
    )


@router.get("/portfolio")
async def portfolio(
    request: Request,
    response: Response,
    account: str | None = Query(None, min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    auth_client: AuthClient = Depends(get_auth_client),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    if account is not None:
        return RedirectResponse(
            url=f"/accounts/holdings?{request.url.query}",
            status_code=status.HTTP_308_PERMANENT_REDIRECT,
        )
    user = await require_user(request, response, auth_client)
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)

    view = await _load_portfolio_view(portfolio_client, user.user_id)
    series = () if view.returns is None else view.returns.series
    rows = [] if view.holdings is None else build_portfolio_holdings(view.holdings.holdings)
    # An unpriced holding says where its ticker's loading stands (ticker onboarding, TK-9).
    unpriced = [r.holding.canonical_ticker for r in rows if not r.holding.priced]
    statuses = await load_statuses(portfolio_client, unpriced)
    return _render(
        request,
        response,
        templates,
        "portfolio.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "nav_active": "portfolio",
            "first_run": view.accounts == [],
            "rows": rows,
            "totals": None if view.holdings is None else view.holdings.totals,
            "holdings_error": _UNAVAILABLE if view.holdings is None else None,
            # A returns failure has its own notice in the chart card, as holdings' does.
            "error": _INCOMPLETE if view.accounts is None or view.unmapped is None else None,
            "returns_error": _UNAVAILABLE if view.returns is None else None,
            "chart_payload": build_portfolio_chart_payload(series),
            "price_notes": {t: holding_price_note(statuses.get(t)) for t in unpriced},
            "unmapped": unmapped_across_brokers(view.unmapped or []),
        },
    )
