"""GET /: the overview across every account the user has (UI-11).

On its own router, like theme.py, because routes.py is close to the repo's 64KB file limit. It
renders through routes.py's _render, so a cookie set earlier in the request (the CSRF cookie, a
renewed session) still reaches the response.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import _render
from web.auth_client.base import AuthUser
from web.portfolio_client.base import Account, PortfolioClient, Return
from web.portfolio_client.errors import PortfolioClientError
from web.portfolio_view import summarize_accounts
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

_ACCOUNTS_UNAVAILABLE = (
    "Can't reach the portfolio service right now, so your accounts can't be shown."
)


async def _load_overview(
    portfolio_client: PortfolioClient, user_id: str
) -> tuple[list[tuple[Account, list[Return] | None]], str | None]:
    """Every account with its latest returns (None where that call failed), plus an error if the
    account list itself couldn't be loaded.

    One list_latest_returns call per account, concurrently, each degrading on its own. An
    AccountNotFound (the account deleted since it was listed) is a PortfolioClientError too, so
    it degrades that row rather than 404ing the whole page.
    """
    try:
        accounts = await portfolio_client.list_accounts(user_id=user_id)
    except PortfolioClientError as exc:
        logger.exception("portfolio service unavailable listing accounts.", exc_info=exc)
        return [], _ACCOUNTS_UNAVAILABLE

    results = await asyncio.gather(
        *(
            portfolio_client.list_latest_returns(user_id=user_id, account_label=a.account_label)
            for a in accounts
        ),
        return_exceptions=True,
    )
    loaded = []
    for account, result in zip(accounts, results, strict=True):
        if isinstance(result, PortfolioClientError):
            logger.exception(
                "portfolio service unavailable listing latest returns for %r.",
                account.account_label,
                exc_info=result,
            )
            result = None
        elif isinstance(result, BaseException):
            raise result
        loaded.append((account, result))
    return loaded, None


@router.get("/")
async def home(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    loaded, list_error = await _load_overview(portfolio_client, user.user_id)
    rows, totals = summarize_accounts(loaded)
    return _render(
        request,
        response,
        templates,
        "home.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "nav_active": "overview",
            "rows": rows,
            "totals": totals,
            "failed_count": sum(1 for r in rows if r.failed),
            "list_error": list_error,
        },
    )
