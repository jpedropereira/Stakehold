"""The Tickers pages (ticker onboarding, TK-8): look a Yahoo Finance symbol up, add it for
data-store to load, and see where each added ticker stands. Admins also see every ticker, and
can disable, re-enable, or retry one.

On its own router, like overview.py, because routes.py is close to the repo's 64KB file limit.
It renders and redirects through routes.py's helpers, so CSRF and cookie handling stay the same
as every other form.

Who may skip the daily limit is decided here, the one place that knows the user is an admin:
`enforce_limit` is sent as `not user.is_admin`, and data-store enforces the rest.
"""

import logging
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import (
    get_portfolio_client,
    get_settings,
    get_templates,
    require_admin,
    require_user,
)
from web.api.routes import (
    _PORTFOLIO_LIST_ERROR,
    _check_csrf,
    _redirect,
    _render,
    _submit_portfolio_form,
)
from web.auth_client.base import AuthUser
from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import (
    InvalidTickerRequest,
    PortfolioClientError,
    TickerChangeRefused,
    TickerLimitReached,
    TickerNotOnProvider,
    TickerProviderUnavailable,
    TrackedTickerNotFound,
)
from web.settings import Settings
from web.ticker_status import LOADING_STATUSES, status_label, ticker_or_none

logger = logging.getLogger(__name__)

router = APIRouter()

_NOT_ON_PROVIDER = (
    "Yahoo Finance doesn't know that symbol. Check the exchange suffix (ASML.AS, not ASML). "
    "Delisted securities may not be available."
)
_PROVIDER_UNAVAILABLE = "Yahoo Finance didn't answer. Try again in a minute."
_NOT_A_SYMBOL = (
    "That isn't a ticker symbol. Use the symbol Yahoo Finance shows, such as ASML.AS or BTC-USD."
)
_PORTFOLIO_UNAVAILABLE = "Can't reach the portfolio service right now."

_ADD_ERRORS: dict[type[PortfolioClientError], tuple[str, int]] = {
    TickerNotOnProvider: (_NOT_ON_PROVIDER, status.HTTP_404_NOT_FOUND),
    InvalidTickerRequest: (
        "That symbol can't be added: it isn't a ticker symbol, or it's an index or a currency "
        "pair, which can't be held.",
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    ),
    TickerLimitReached: (
        "You've added as many new tickers as you can today. Try again tomorrow.",
        status.HTTP_429_TOO_MANY_REQUESTS,
    ),
    TickerProviderUnavailable: (_PROVIDER_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE),
}

_CHANGE_ERRORS: dict[type[PortfolioClientError], tuple[str, int]] = {
    TrackedTickerNotFound: ("That ticker isn't tracked any more.", status.HTTP_404_NOT_FOUND),
    TickerChangeRefused: (
        "That change isn't possible. A ticker from the default list is turned on or off in "
        "tracked_tickers.yaml, and only a ticker that couldn't load can be retried.",
        status.HTTP_409_CONFLICT,
    ),
}

# Each admin action: what it sends, and the sentence confirming it on the list.
_CHANGES = {
    "disable": (
        {"enabled": False},
        "{ticker} disabled. Its prices stop updating; its history stays.",
    ),
    "enable": ({"enabled": True}, "{ticker} enabled."),
    "retry": ({"retry": True}, "{ticker} is queued to load again."),
}


def _outcome(request: Request) -> str | None:
    """The banner after a redirect back to the list, read from its query string. Only a
    ticker-shaped value is ever echoed."""
    params = request.query_params
    added = ticker_or_none(params.get("added"))
    if added:
        return f"{added} added. Prices usually load within a few minutes."
    already = ticker_or_none(params.get("already"))
    if already:
        return f"{already} was already in Stakehold. Nothing new was added."
    changed = ticker_or_none(params.get("changed"))
    change = _CHANGES.get(params.get("change", ""))
    if changed and change:
        return change[1].format(ticker=changed)
    return None


async def _list_context(portfolio_client: PortfolioClient, requested_by: str | None) -> dict:
    """A list of tracked tickers: every one when `requested_by` is None, else those that user
    added. None (not []) when it couldn't be loaded, so the page never says "none added" during
    an outage. tickers.html shows an admin every ticker, a user their own; tickers_new.html
    shows everyone their own (UX-11), as context for the add, not a second admin list."""
    try:
        tickers = await portfolio_client.list_tracked_tickers(requested_by=requested_by)
    except PortfolioClientError as exc:
        logger.exception("portfolio service unavailable listing tracked tickers.", exc_info=exc)
        return {"tickers": None, "list_error": _PORTFOLIO_LIST_ERROR}
    return {"tickers": tickers, "list_error": None}


def _shown_on_tickers(user: AuthUser) -> str | None:
    """Whose tickers /tickers lists: an admin's is every ticker."""
    return None if user.is_admin else user.user_id


def _page(user: AuthUser, csrf_token: str) -> dict:
    return {
        "user": user,
        "csrf_token": csrf_token,
        "nav_active": "tickers",
        "status_label": status_label,
        "loading_statuses": LOADING_STATUSES,
    }


@router.get("/tickers")
async def tickers_page(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    return _render(
        request,
        response,
        templates,
        "tickers.html",
        {
            **_page(user, csrf_token),
            "error": None,
            "outcome": _outcome(request),
            **await _list_context(portfolio_client, _shown_on_tickers(user)),
        },
    )


@router.get("/tickers/new")
async def tickers_new_page(
    request: Request,
    response: Response,
    symbol: str = Query(default="", max_length=40),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """The lookup is a GET form, so it works without JS and a result can be bookmarked. Adding
    is the separate POST below, from the confirmation the lookup shows."""
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    context = {
        **_page(user, csrf_token),
        "symbol": symbol.strip(),
        "lookup": None,
        "error": None,
        # Set by tickers_add's redirect back here, so the new row shows beside the form.
        "outcome": _outcome(request),
        **await _list_context(portfolio_client, user.user_id),
    }
    status_code = status.HTTP_200_OK
    if context["symbol"]:
        try:
            context["lookup"] = await portfolio_client.lookup_ticker(context["symbol"])
        except InvalidTickerRequest:
            context["lookup_error"] = _NOT_A_SYMBOL
            status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        except PortfolioClientError as exc:
            logger.exception("ticker lookup: portfolio service unavailable.", exc_info=exc)
            context["lookup_error"] = _PORTFOLIO_UNAVAILABLE
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return _render(
        request, response, templates, "tickers_new.html", context, status_code=status_code
    )


@router.post("/tickers")
async def tickers_add(
    request: Request,
    response: Response,
    # default="", not Form(...) -- see routes.accounts_submit.
    symbol: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    page = {**_page(user, csrf_token), "symbol": symbol.strip(), "lookup": None, "outcome": None}
    page.pop("csrf_token")

    async def load_list() -> dict:
        # Lazy: only an error re-render shows the list; a success redirects.
        return await _list_context(portfolio_client, user.user_id)

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "tickers_new.html",
        "add_ticker",
        csrf_token,
        extra_context_loader=load_list,
        **page,
    )
    if csrf_error is not None:
        return csrf_error

    def added(result) -> Response:
        key = "added" if result.created else "already"
        # Back to the add page, whose list beside the form now holds the new row (UX-11).
        return _redirect(request, response, f"/tickers/new?{key}={quote(result.ticker.ticker)}")

    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="tickers_new.html",
        action_name="add_ticker",
        call=lambda: portfolio_client.add_tracked_ticker(
            ticker=symbol.strip(), requested_by=user.user_id, enforce_limit=not user.is_admin
        ),
        error_messages=_ADD_ERRORS,
        on_success=added,
        extra_context_loader=load_list,
        **page,
    )


@router.post("/tickers/{ticker}/{action}")
async def tickers_change(
    request: Request,
    response: Response,
    ticker: str,
    action: str,
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_admin),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """An admin's disable, enable or retry. POST, since a plain HTML form can't send PATCH."""
    if action not in _CHANGES:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    change, _ = _CHANGES[action]
    page = _page(user, csrf_token)
    page.pop("csrf_token")

    async def load_list() -> dict:
        return {"outcome": None, **await _list_context(portfolio_client, _shown_on_tickers(user))}

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "tickers.html",
        "change_ticker",
        csrf_token,
        extra_context_loader=load_list,
        **page,
    )
    if csrf_error is not None:
        return csrf_error
    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="tickers.html",
        action_name="change_ticker",
        call=lambda: portfolio_client.update_tracked_ticker(ticker=ticker, **change),
        error_messages=_CHANGE_ERRORS,
        on_success=lambda _row: _redirect(
            request, response, f"/tickers?changed={quote(ticker)}&change={action}"
        ),
        extra_context_loader=load_list,
        **page,
    )
