"""GET /accounts/holdings: one account's holdings, summary tiles and returns chart.

It was served at /portfolio?account=X until PF-2, which freed /portfolio for the combined page
across accounts; api/portfolio.py redirects the old URL here.

On its own router, like overview.py, because routes.py is close to the repo's 64KB file limit.
It renders through routes.py's _render, so a cookie set earlier in the request (the CSRF cookie, a
renewed session) still reaches the response, and shares routes.py's account-page header helpers
with the other per-account pages.
"""

import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import (
    _account_header_context,
    _account_page_context,
    _render,
    _render_account_not_found,
)
from web.auth_client.base import AuthUser
from web.portfolio_client.base import (
    PortfolioClient,
    Position,
    Return,
    UnmappedInstrument,
)
from web.portfolio_client.errors import AccountNotFound, PortfolioClientError
from web.portfolio_view import (
    aggregate_portfolio_returns,
    build_chart_payload,
    build_holdings,
    filter_open_positions,
    summarize_latest_returns,
    unmapped_for_broker,
)
from web.settings import Settings
from web.ticker_status import holding_price_note, load_statuses

logger = logging.getLogger(__name__)

router = APIRouter()


_PORTFOLIO_VIEW_ERROR = (
    "Can't reach the portfolio service right now — this section may be incomplete."
)


@dataclass(frozen=True)
class _HoldingsViewData:
    positions: list[Position]
    returns: list[Return]
    latest_returns: list[Return]
    unmapped: list[UnmappedInstrument]
    # Which calls degraded, so the page doesn't read a failed call's empty list as "none yet".
    failed: frozenset[str]
    error: str | None


async def _load_holdings_view(
    portfolio_client: PortfolioClient, user_id: str, account_label: str
) -> _HoldingsViewData:
    """Fetches this account's positions, return history and latest returns, plus the user's
    unmapped instruments (UI-7), concurrently, tolerating any of them
    failing independently -- except AccountNotFound, which is re-raised rather than degraded: a
    bad account_label has nothing partial to show for any call, so the caller renders a distinct
    404 instead of the generic "service unavailable" banner."""
    calls = {
        "positions": portfolio_client.list_positions(user_id=user_id, account_label=account_label),
        "returns": portfolio_client.list_returns(user_id=user_id, account_label=account_label),
        "latest returns": portfolio_client.list_latest_returns(
            user_id=user_id, account_label=account_label
        ),
        "unmapped instruments": portfolio_client.list_unmapped_instruments(user_id=user_id),
    }
    results = dict(
        zip(calls, await asyncio.gather(*calls.values(), return_exceptions=True), strict=True)
    )
    for result in results.values():
        if isinstance(result, AccountNotFound):
            raise result

    failed = set()
    for name, result in results.items():
        if isinstance(result, PortfolioClientError):
            logger.exception("portfolio service unavailable listing %s.", name, exc_info=result)
            results[name] = []
            failed.add(name)
        elif isinstance(result, BaseException):
            raise result
    return _HoldingsViewData(
        positions=results["positions"],
        returns=results["returns"],
        latest_returns=results["latest returns"],
        unmapped=results["unmapped instruments"],
        failed=frozenset(failed),
        error=_PORTFOLIO_VIEW_ERROR if failed else None,
    )


@router.get("/accounts/holdings")
async def account_holdings_page(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    header, view = await asyncio.gather(
        _account_header_context(portfolio_client, user.user_id, account),
        _load_holdings_view(portfolio_client, user.user_id, account),
        return_exceptions=True,
    )
    if isinstance(header, BaseException):
        raise header
    if isinstance(view, AccountNotFound) or header["account_known"] is False:
        return _render_account_not_found(
            request, response, templates, user=user, csrf_token=csrf_token
        )
    if isinstance(view, BaseException):
        raise view

    holdings = build_holdings(filter_open_positions(view.positions), view.latest_returns)
    prices_known = "latest returns" not in view.failed
    # A holding with no return row says where its ticker's loading stands (ticker onboarding):
    # asked only about those holdings, and only when the returns call answered.
    awaiting = [h.canonical_ticker for h in holdings if prices_known and h.awaiting_prices]
    statuses = await load_statuses(portfolio_client, awaiting)
    return_points = aggregate_portfolio_returns(view.returns)
    return _render(
        request,
        response,
        templates,
        "account_holdings.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "account_label": account,
            "summary": summarize_latest_returns(view.latest_returns),
            "holdings": holdings,
            # A holding with no return row is "waiting for prices" only if the call answered.
            "prices_known": prices_known,
            "price_notes": {t: holding_price_note(statuses.get(t)) for t in awaiting},
            "unmapped": unmapped_for_broker(
                view.unmapped, header["account"].broker if header["account"] else None
            ),
            "return_points": return_points,
            "chart_payload": build_chart_payload(return_points),
            "error": view.error,
            **_account_page_context("holdings"),
            **header,
        },
    )
