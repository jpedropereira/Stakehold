import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from web.api.csrf import CsrfInvalid, ensure_csrf_cookie, verify_csrf
from web.api.deps import (
    client_ip,
    get_auth_client,
    get_portfolio_client,
    get_settings,
    get_templates,
    optional_user,
    require_admin,
    require_user,
)
from web.api.htmx import htmx_or_redirect, is_htmx
from web.auth_client.base import AuthClient, AuthUser
from web.auth_client.errors import (
    AuthClientError,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRequest,
    RateLimited,
)
from web.portfolio_client.base import (
    PortfolioClient,
    TransactionDeleteResult,
    TransactionWriteResult,
)
from web.portfolio_client.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidPortfolioRequest,
    PortfolioClientError,
    TickerMappingNotFound,
)
from web.rebuild_outcome import rebuild_outcome
from web.settings import Settings
from web.ticker_names import load_names
from web.transaction_counts import load_transaction_counts

logger = logging.getLogger(__name__)

router = APIRouter()
health_router = APIRouter()

# Mirrors portfolio/transactions/base.py's TRANSACTION_TYPES -- not imported
# directly, since web talks to portfolio only over HTTP (it has no Python dependency on the
# portfolio package). Kept here as the one place this contract is duplicated, the same way
# _SESSION_COOKIE_NAME below documents auth's cookie name as part of the HTTP contract rather
# than a shared import.
_TRANSACTION_TYPES = ("BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FX", "FEE", "ADJUSTMENT")
# Manual entry only ever submits a data-store ticker picked from the list, never an ISIN/SYMBOL
# -- those need a ticker mapping, which is for broker imports. See portfolio/docs/schema.md.
_MANUAL_IDENTIFIER_KIND = "TICKER"

# Which transaction types each type-dependent manual-entry field applies to, and which of those
# require it -- read by static/transaction_form.js from the data-types/data-required-for
# attributes the template renders, so this is the one place the mapping lives. Mirrors
# portfolio's transactions/base.py validate() and pipeline.py _manual_amount (UI-9):
# - a BUY/SELL needs an instrument, a quantity and a price, and its amount is derived, so the
#   amount field is hidden for one rather than offered as a figure that can only match;
# - every other type is a cash event whose amount is the only figure there is;
# - fees only feed a BUY/SELL's derived amount;
# - a DIVIDEND may name its instrument, which is how returns attributes it to a holding;
# - ADJUSTMENT is unconstrained by validate(), so it shows every field.
# A field not listed here (type, reference, date, currency) is shown for every type, and FX rate
# follows the currency instead (the template's data-hide-for-currency). Without JS every field
# shows and none is required by the browser: portfolio's validation is the source of truth.
_TRADE_TYPES = ("BUY", "SELL")
_CASH_TYPES = tuple(t for t in _TRANSACTION_TYPES if t not in _TRADE_TYPES)
_FIELD_TYPES: dict[str, tuple[str, ...]] = {
    "native_amount": _CASH_TYPES,
    "amount_derived": _TRADE_TYPES,
    "fees_native": (*_TRADE_TYPES, "ADJUSTMENT"),
    "instrument_identifier": (*_TRADE_TYPES, "DIVIDEND", "ADJUSTMENT"),
    "quantity": (*_TRADE_TYPES, "ADJUSTMENT"),
    "price": (*_TRADE_TYPES, "ADJUSTMENT"),
}
_FIELD_REQUIRED_FOR: dict[str, tuple[str, ...]] = {
    "native_amount": _CASH_TYPES,
    "instrument_identifier": _TRADE_TYPES,
    "quantity": _TRADE_TYPES,
    "price": _TRADE_TYPES,
}
# The currency field's suggestions, EUR first as the default. A web-side constant because
# nothing exposes data-store's own list (its config/currencies.yaml): neither data-store nor
# portfolio has an endpoint for it, and this chunk adds none. Suggestions only -- the field
# still takes any code, and portfolio accepts any ISO 4217 currency given an FX rate -- so a
# deployment tracking a different set only gets a less useful list, never a blocked entry.
_SUGGESTED_CURRENCIES = ("EUR", "USD", "GBP", "CHF", "PLN")

# Matches portfolio's own cap (portfolio/api/routes.py's _MAX_UPLOAD_BYTES) -- read here, before
# the bytes are ever forwarded, so an oversized upload doesn't sit fully buffered in web's memory
# only to be rejected one hop later.
_MAX_UPLOAD_MB = 20
_MAX_UPLOAD_BYTES = _MAX_UPLOAD_MB * 1024 * 1024

# Must match auth/api/cookies.py's COOKIE_NAME and cookie flags. Not imported directly: web and
# auth share no code beyond the `shared` infrastructure package -- they communicate only through
# the HTTP API, and the cookie's name and flags are part of that contract, same as its own
# documented endpoint surface.
_SESSION_COOKIE_NAME = "stakehold_session"

# Every AuthClientError subtype login/signup can be shown to the visitor as a specific message,
# keyed by exception type. Anything an AuthClient implementation raises that isn't in the map
# (AuthUnavailable, or a subtype auth grows in the future) falls through to _DEFAULT_AUTH_ERROR,
# so a new/unrecognized failure degrades to "the service is unavailable" rather than an unhandled
# 500 -- the message and status code stay in exactly one place per action instead of duplicated
# across a hand-written except chain.
_DEFAULT_AUTH_ERROR = (
    "Can't reach the sign-in service right now.",
    status.HTTP_503_SERVICE_UNAVAILABLE,
)


@health_router.get("/health")
def health() -> dict[str, str]:
    # Deliberately doesn't probe auth: web is up whether or not auth is, and a dependent
    # healthcheck would make a transient auth blip restart web too. See docs/architecture.md.
    return {"status": "ok"}


def _finish(sub_response: Response, built: Response) -> Response:
    """Copies headers set on the dependency-injected `response` param onto the Response this
    route actually returns.

    FastAPI only auto-merges the injected sub-response's headers when a route returns a plain
    value it serializes itself; a route (like every one here) that returns a Response subclass
    directly -- TemplateResponse, RedirectResponse -- is used as-is, with no merge. Without this,
    a cookie set via `response.set_cookie`/`ensure_csrf_cookie`/`response.headers.append`
    earlier in the same request would be silently dropped. Every route below goes through
    `_render`/`_redirect` rather than building a Response by hand, so this is the one place that
    merge has to happen.
    """
    built.headers.raw.extend(sub_response.headers.raw)
    return built


def _render(
    request: Request,
    response: Response,
    templates,
    template_name: str,
    context: dict,
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    built = templates.TemplateResponse(request, template_name, context, status_code=status_code)
    return _finish(response, built)


def _redirect(request: Request, response: Response, url: str) -> Response:
    return _finish(response, htmx_or_redirect(request, url))


# The bold lead word of each form's error banner (the charter's ink rule, chapter II), by the
# template the form lives on. The messages themselves are full sentences, kept as they are; the
# lead says which action failed. Keyed by template rather than passed by each caller because
# _render_form_error is the one path every form error takes, CSRF failures included.
_ERROR_LEADS = {
    "login.html": "Couldn't log in.",
    "signup.html": "Couldn't sign up.",
    "accounts.html": "Couldn't create the account.",
    "transactions_new.html": "Couldn't save.",
    "transactions_import.html": "Couldn't import.",
    "admin_mappings.html": "Couldn't change the mapping.",
    "tickers_new.html": "Couldn't add the ticker.",
    "tickers.html": "Couldn't change the ticker.",
    "transaction_delete.html": "Couldn't delete.",
    "account_delete.html": "Couldn't delete.",
}


async def _render_form_error(
    request: Request,
    response: Response,
    settings: Settings,
    templates,
    template_name: str,
    message: str,
    status_code: int,
    *,
    extra_context_loader=None,
    **extra_context,
) -> Response:
    """Re-renders a submitted form with an error, as an htmx partial swap or a full page.

    `extra_context` carries whatever a form beyond login/signup needs to redisplay correctly on
    the full-page path -- e.g. the transaction/import forms' `account_label`, which the hidden
    field must keep echoing after an error or a resubmit would silently target the wrong account,
    or `user`, which every full-page template needs for base.html's nav/logout block. The htmx
    partial swap never needs either: it only ever re-renders `_form_errors.html`.

    `extra_context_loader`, if given, is an async callable returning additional context -- used
    instead of `extra_context` for anything that costs an I/O call (e.g. re-fetching accounts/
    brokers) so that cost is paid only on this, the actual error-render path, never on success.
    """
    if is_htmx(request):
        built = _render(
            request,
            response,
            templates,
            "partials/_form_errors.html",
            {"error": message, "error_lead": _ERROR_LEADS[template_name]},
            status_code=status_code,
        )
        # Marks this 4xx/5xx as the app's own deliberate error partial, not a server crash --
        # htmx-error-swap.js checks for this header before forcing the swap, so an unmapped
        # exception's default error page still surfaces as a normal htmx error instead of
        # being silently rendered into the form.
        built.headers["X-Form-Error"] = "true"
        return built
    token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    context = {
        "csrf_token": token,
        "error": message,
        "error_lead": _ERROR_LEADS[template_name],
        **extra_context,
    }
    if extra_context_loader is not None:
        context.update(await extra_context_loader())
    return _render(
        request,
        response,
        templates,
        template_name,
        context,
        status_code=status_code,
    )


async def _check_csrf(
    request: Request,
    response: Response,
    settings: Settings,
    templates,
    template_name: str,
    action_name: str,
    csrf_token: str,
    *,
    extra_context_loader=None,
    **extra_context,
) -> Response | None:
    """Returns an error Response if the CSRF check fails, otherwise None."""
    try:
        verify_csrf(request, form_token=csrf_token, cookie_secure=settings.cookie_secure)
    except CsrfInvalid:
        logger.warning("%s: CSRF check failed.", action_name)
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            template_name,
            "Something went wrong with that submission. Refresh the page and try again.",
            status.HTTP_400_BAD_REQUEST,
            extra_context_loader=extra_context_loader,
            **extra_context,
        )
    return None


async def _submit_auth_form(
    request: Request,
    response: Response,
    settings: Settings,
    templates,
    *,
    template_name: str,
    action_name: str,
    call,
    error_messages: dict[type[AuthClientError], tuple[str, int]],
) -> Response:
    """Runs an auth_client call and renders the appropriate outcome.

    Shared by login_submit/signup_submit: both do CSRF-check, call auth, map the specific
    AuthClientError raised to a message via `error_messages`, and either redirect home with the
    proxied session cookie or re-render the form with an error -- differing only in which
    template, which auth call, and which subclasses map to which message.
    """
    try:
        result = await call()
    except AuthClientError as exc:
        message, status_code = error_messages.get(type(exc), _DEFAULT_AUTH_ERROR)
        if type(exc) not in error_messages:
            logger.exception("%s: auth service unavailable.", action_name)
        return await _render_form_error(
            request, response, settings, templates, template_name, message, status_code
        )

    response.headers.append("set-cookie", result.set_cookie)
    return _redirect(request, response, "/")


_DEFAULT_PORTFOLIO_ERROR = (
    "Can't reach the portfolio service right now.",
    status.HTTP_503_SERVICE_UNAVAILABLE,
)


async def _submit_portfolio_form(
    request: Request,
    response: Response,
    settings: Settings,
    templates,
    *,
    template_name: str,
    action_name: str,
    call,
    error_messages: dict[type[PortfolioClientError], tuple[str, int]],
    on_success,
    extra_context_loader=None,
    **extra_context,
) -> Response:
    """Runs a portfolio_client call and renders the appropriate outcome.

    Mirrors _submit_auth_form's shape, with one deliberate difference:
    InvalidPortfolioRequest's message is shown to the visitor verbatim rather than looked up in
    `error_messages` -- see portfolio_client/errors.py's own docstring for why (portfolio's
    validation messages are meant to be read as-is, unlike auth's canned per-type copy).
    """
    try:
        result = await call()
    except InvalidPortfolioRequest as exc:
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            template_name,
            str(exc),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            extra_context_loader=extra_context_loader,
            **extra_context,
        )
    except PortfolioClientError as exc:
        message, status_code = error_messages.get(type(exc), _DEFAULT_PORTFOLIO_ERROR)
        if type(exc) not in error_messages:
            logger.exception("%s: portfolio service unavailable.", action_name)
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            template_name,
            message,
            status_code,
            extra_context_loader=extra_context_loader,
            **extra_context,
        )
    return on_success(result)


# GET / (the overview) lives in overview.py.


@router.get("/admin")
async def admin_page(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_admin),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    return _render(
        request,
        response,
        templates,
        "admin.html",
        {"user": user, "csrf_token": csrf_token, "nav_active": "admin"},
    )


# --- admin: ticker mappings ------------------------------------------------------

_MAPPING_ERRORS: dict[type[PortfolioClientError], tuple[str, int]] = {
    TickerMappingNotFound: ("That mapping was already deleted.", status.HTTP_404_NOT_FOUND),
}


def _query_count(request: Request, name: str) -> int:
    raw = request.query_params.get(name, "")
    return int(raw) if raw.isdigit() else 0


def _mapping_outcome(request: Request) -> tuple[str, str] | None:
    """The banner after a redirect back from a save or delete, read from its query string: its
    bold lead, then the detail."""
    if request.query_params.get("saved") == "1":
        done = "Mapping saved."
    elif request.query_params.get("deleted") == "1":
        done = "Mapping deleted."
    else:
        return None
    detail = f"Positions rebuilt for {_query_count(request, 'rebuilt')} account(s)."
    failed = _query_count(request, "failed")
    if failed:
        detail += (
            f" {failed} rebuild(s) failed — the mapping is saved; rebuild those accounts' "
            "positions to retry."
        )
    return done, detail


async def _mappings_context(portfolio_client: PortfolioClient) -> dict:
    """Everything admin_mappings.html shows, fetched concurrently. Each list degrades on its own
    to empty plus a notice, the _list_accounts_and_brokers pattern, so an outage never looks
    like "no mappings" or "nothing to map"."""
    mappings, unmapped = await asyncio.gather(
        portfolio_client.list_ticker_mappings(),
        portfolio_client.list_all_unmapped_instruments(),
        return_exceptions=True,
    )
    list_error = None
    if isinstance(mappings, PortfolioClientError):
        logger.exception(
            "portfolio service unavailable listing ticker mappings.", exc_info=mappings
        )
        mappings, list_error = None, _PORTFOLIO_LIST_ERROR
    elif isinstance(mappings, BaseException):
        raise mappings
    if isinstance(unmapped, PortfolioClientError):
        logger.exception(
            "portfolio service unavailable listing unmapped instruments.", exc_info=unmapped
        )
        unmapped, list_error = None, _PORTFOLIO_LIST_ERROR
    elif isinstance(unmapped, BaseException):
        raise unmapped
    tickers, tickers_notice = await _load_tickers(portfolio_client)
    return {
        # None (not []) when the list couldn't be loaded, so the template can tell "empty" from
        # "unknown" and say only the true one.
        "mappings": mappings,
        "unmapped": unmapped,
        "list_error": list_error,
        "tickers": tickers,
        "tickers_notice": tickers_notice,
    }


@router.get("/admin/mappings")
async def admin_mappings_page(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_admin),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    return _render(
        request,
        response,
        templates,
        "admin_mappings.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "nav_active": "admin",
            "error": None,
            "outcome": _mapping_outcome(request),
            **await _mappings_context(portfolio_client),
        },
    )


def _redirect_after_mapping_change(request: Request, response: Response, action: str, result):
    return _redirect(
        request,
        response,
        f"/admin/mappings?{action}=1&rebuilt={result.accounts_rebuilt}"
        f"&failed={result.rebuilds_failed}",
    )


async def _submit_mapping_change(
    request: Request,
    response: Response,
    settings: Settings,
    templates,
    *,
    user: AuthUser,
    portfolio_client: PortfolioClient,
    csrf_token: str,
    action_name: str,
    call,
    redirect_action: str,
) -> Response:
    """The CSRF check, call and outcome both mapping forms share. An error re-renders the whole
    page, lists and all, with portfolio's own message (or _MAPPING_ERRORS')."""

    async def load_page_context() -> dict:
        return {"outcome": None, **await _mappings_context(portfolio_client)}

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "admin_mappings.html",
        action_name,
        csrf_token,
        user=user,
        nav_active="admin",
        extra_context_loader=load_page_context,
    )
    if csrf_error is not None:
        return csrf_error
    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="admin_mappings.html",
        action_name=action_name,
        call=call,
        error_messages=_MAPPING_ERRORS,
        on_success=lambda result: _redirect_after_mapping_change(
            request, response, redirect_action, result
        ),
        user=user,
        nav_active="admin",
        extra_context_loader=load_page_context,
    )


@router.post("/admin/mappings")
async def admin_mappings_set(
    request: Request,
    response: Response,
    # default="", not Form(...) -- see accounts_submit.
    identifier_kind: str = Form(default=""),
    identifier: str = Form(default=""),
    broker: str = Form(default=""),
    canonical_ticker: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_admin),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    return await _submit_mapping_change(
        request,
        response,
        settings,
        templates,
        user=user,
        portfolio_client=portfolio_client,
        csrf_token=csrf_token,
        action_name="set_ticker_mapping",
        # A blank broker is "no broker" -- what an ISIN mapping needs. portfolio enforces which
        # kind needs one, so its message is the one the admin sees.
        call=lambda: portfolio_client.set_ticker_mapping(
            identifier_kind=identifier_kind,
            identifier=identifier,
            broker=broker.strip() or None,
            canonical_ticker=canonical_ticker,
            updated_by=user.user_id,
        ),
        redirect_action="saved",
    )


@router.post("/admin/mappings/{mapping_id}/delete")
async def admin_mappings_delete(
    request: Request,
    response: Response,
    mapping_id: int,
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_admin),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    # POST, not DELETE: a plain HTML form can only send GET or POST, and the page has no script.
    return await _submit_mapping_change(
        request,
        response,
        settings,
        templates,
        user=user,
        portfolio_client=portfolio_client,
        csrf_token=csrf_token,
        action_name="delete_ticker_mapping",
        call=lambda: portfolio_client.delete_ticker_mapping(
            mapping_id=mapping_id, updated_by=user.user_id
        ),
        redirect_action="deleted",
    )


@router.get("/login")
async def login_form(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser | None = Depends(optional_user),
):
    if user is not None:
        return _redirect(request, response, "/")
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    return _render(
        request, response, templates, "login.html", {"csrf_token": csrf_token, "error": None}
    )


_LOGIN_ERRORS: dict[type[AuthClientError], tuple[str, int]] = {
    InvalidCredentials: ("That email and password don't match.", status.HTTP_401_UNAUTHORIZED),
    RateLimited: (
        "Too many attempts. Try again in a few minutes.",
        status.HTTP_429_TOO_MANY_REQUESTS,
    ),
    InvalidRequest: (
        "Check your email and password and try again.",
        status.HTTP_400_BAD_REQUEST,
    ),
}


@router.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    auth_client: AuthClient = Depends(get_auth_client),
):
    csrf_error = await _check_csrf(
        request, response, settings, templates, "login.html", "login", csrf_token
    )
    if csrf_error is not None:
        return csrf_error

    return await _submit_auth_form(
        request,
        response,
        settings,
        templates,
        template_name="login.html",
        action_name="login",
        call=lambda: auth_client.login(
            email=email, password=password, client_ip=client_ip(request)
        ),
        error_messages=_LOGIN_ERRORS,
    )


@router.get("/signup")
async def signup_form(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser | None = Depends(optional_user),
):
    if user is not None:
        return _redirect(request, response, "/")
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    return _render(
        request, response, templates, "signup.html", {"csrf_token": csrf_token, "error": None}
    )


_SIGNUP_ERRORS: dict[type[AuthClientError], tuple[str, int]] = {
    EmailAlreadyRegistered: ("That email is already registered.", status.HTTP_409_CONFLICT),
    RateLimited: (
        "Too many attempts. Try again in a few minutes.",
        status.HTTP_429_TOO_MANY_REQUESTS,
    ),
    InvalidRequest: (
        "Check your email, password, and name, and try again.",
        status.HTTP_400_BAD_REQUEST,
    ),
}


@router.post("/signup")
async def signup_submit(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    csrf_token: str = Form(...),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    auth_client: AuthClient = Depends(get_auth_client),
):
    csrf_error = await _check_csrf(
        request, response, settings, templates, "signup.html", "signup", csrf_token
    )
    if csrf_error is not None:
        return csrf_error

    return await _submit_auth_form(
        request,
        response,
        settings,
        templates,
        template_name="signup.html",
        action_name="signup",
        call=lambda: auth_client.signup(
            email=email, password=password, display_name=display_name, client_ip=client_ip(request)
        ),
        error_messages=_SIGNUP_ERRORS,
    )


@router.post("/logout")
async def logout_submit(
    request: Request,
    response: Response,
    csrf_token: str = Form(...),
    settings: Settings = Depends(get_settings),
    auth_client: AuthClient = Depends(get_auth_client),
):
    try:
        verify_csrf(request, form_token=csrf_token, cookie_secure=settings.cookie_secure)
    except CsrfInvalid:
        logger.warning("logout: CSRF check failed.")
        return _redirect(request, response, "/")

    cookie_header = request.headers.get("cookie")
    if cookie_header is not None:
        try:
            await auth_client.logout(cookie_header=cookie_header)
        except AuthClientError:
            # Logging out is a one-way door for the user's intent: even if auth couldn't be
            # reached, clear the cookie locally so the browser stops sending a stale session.
            logger.exception("logout: auth service unavailable.")

    # Flags must match auth's own set_session_cookie (auth/api/cookies.py) -- a browser only
    # clears a cookie when the deleting Set-Cookie's attributes are compatible with how it was
    # set, and a mismatch here (e.g. missing Secure on a __Host--prefixed cookie) would make the
    # browser silently keep sending the stale session cookie after logout.
    response.delete_cookie(
        key=_SESSION_COOKIE_NAME,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return _redirect(request, response, "/login")


# --- accounts ---------------------------------------------------------------

_ACCOUNT_ERRORS: dict[type[PortfolioClientError], tuple[str, int]] = {
    AccountLabelConflict: (
        "That label is already used with a different broker.",
        status.HTTP_409_CONFLICT,
    ),
}

_PORTFOLIO_LIST_ERROR = "Can't reach the portfolio service right now — the list may be incomplete."


async def _list_accounts_and_brokers(
    portfolio_client: PortfolioClient, user_id: str
) -> tuple[list, list, str | None]:
    """Fetches the accounts/brokers lists accounts.html needs, concurrently and tolerating
    either call failing independently, so a portfolio outage degrades to an inline notice with
    empty lists rather than a 500 or (worse) a silent, indistinguishable-from-empty page."""
    accounts_result, brokers_result = await asyncio.gather(
        portfolio_client.list_accounts(user_id=user_id),
        portfolio_client.list_brokers(),
        return_exceptions=True,
    )
    error = None
    if isinstance(accounts_result, PortfolioClientError):
        logger.exception(
            "portfolio service unavailable listing accounts.", exc_info=accounts_result
        )
        accounts_result, error = [], _PORTFOLIO_LIST_ERROR
    elif isinstance(accounts_result, BaseException):
        raise accounts_result
    if isinstance(brokers_result, PortfolioClientError):
        logger.exception("portfolio service unavailable listing brokers.", exc_info=brokers_result)
        brokers_result, error = [], _PORTFOLIO_LIST_ERROR
    elif isinstance(brokers_result, BaseException):
        raise brokers_result
    return accounts_result, brokers_result, error


async def _account_header_context(
    portfolio_client: PortfolioClient, user_id: str, account_label: str
) -> dict:
    """What partials/_account_header.html needs: the account (for its broker) and every account
    the user has (for the switcher).

    `account_known` is True/False once the list has loaded, and None when portfolio couldn't be
    reached. Callers 404 only on False: an outage must never tell the visitor a real account
    doesn't exist, so it degrades to a header with the label alone and no switcher.
    """
    try:
        accounts = await portfolio_client.list_accounts(user_id=user_id)
    except PortfolioClientError as exc:
        logger.exception("portfolio service unavailable listing accounts.", exc_info=exc)
        return {"account": None, "accounts": [], "account_known": None}
    account = next((a for a in accounts if a.account_label == account_label), None)
    return {"account": account, "accounts": accounts, "account_known": account is not None}


def _account_page_context(account_nav_active: str) -> dict:
    """The context every account-scoped page shares beyond the header's fetched data: which
    top-nav section and which account sub-nav entry are current."""
    return {"nav_active": "accounts", "account_nav_active": account_nav_active}


def _render_account_not_found(
    request: Request, response: Response, templates, *, user: AuthUser, csrf_token: str
) -> Response:
    return _render(
        request,
        response,
        templates,
        "account_not_found.html",
        {"user": user, "csrf_token": csrf_token, "nav_active": "accounts"},
        status_code=status.HTTP_404_NOT_FOUND,
    )


# The broker dropdown's "Custom" option: a broker with no importer installed, whose name the
# visitor types instead. Not a broker key itself -- accounts_submit swaps in the typed name.
# Dunder-wrapped so no installed plugin's key can collide with it.
CUSTOM_BROKER = "__custom__"


async def _custom_broker_error(portfolio_client: PortfolioClient, name: str) -> str | None:
    """Why a typed custom broker name can't be used, or None if it can.

    A name matching an installed broker (ignoring case) is refused: the account would get a
    broker that differs from the installed key only in case, so none of that broker's importers
    would apply to it. If the installed brokers can't be listed, the name is let through rather
    than blocking account creation on an outage -- the worst case is an account with no
    importers, which its import page then says plainly."""
    if not name:
        return "Enter a name for the custom broker."
    try:
        brokers = await portfolio_client.list_brokers()
    except PortfolioClientError as exc:
        logger.warning("create_account: couldn't list brokers to check a custom name -- %s", exc)
        return None
    match = next((b.broker for b in brokers if b.broker.casefold() == name.casefold()), None)
    if match is not None:
        return f"{match} is in the broker list — choose it there so its files can be imported."
    return None


@router.get("/accounts")
async def accounts_page(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    accounts, brokers, list_error = await _list_accounts_and_brokers(portfolio_client, user.user_id)
    return _render(
        request,
        response,
        templates,
        "accounts.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "nav_active": "accounts",
            "accounts": accounts,
            "brokers": brokers,
            # Apart from `error`, which is the create form's own: the form sits in a disclosure
            # that is collapsed once the visitor has accounts, and an outage notice must never
            # be hidden in it.
            "list_error": list_error,
            "error": None,
            # For each Delete link's warning (api/deletion.py has the delete itself).
            "transaction_counts": await load_transaction_counts(
                portfolio_client, user.user_id, [a.account_label for a in accounts]
            ),
            # Set by deletion.py's redirect: 1 if it deleted the account, 0 if it was gone.
            "deleted": _query_int(request, "deleted"),
            "deleted_transactions": _query_int(request, "transactions") or 0,
        },
    )


@router.post("/accounts")
async def accounts_submit(
    request: Request,
    response: Response,
    # default="", not Form(...): FastAPI/Starlette's Form parameter treats an empty-string
    # value as *missing* rather than present-but-blank, which would 422 with a raw JSON body
    # before this handler ever runs, bypassing the CSRF check and portfolio's own
    # blank-broker/blank-label validation (InvalidBroker/InvalidPortfolioRequest) that are meant
    # to render a proper error page for exactly this case.
    broker: str = Form(default=""),
    custom_broker: str = Form(default=""),
    account_label: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    async def load_error_context() -> dict:
        # Any full-page error re-render of accounts.html below -- CSRF failure, a form/portfolio
        # error -- needs the visitor's existing accounts/brokers, the same as the GET route, or
        # the page falsely claims the account list is empty. Loaded lazily (only when an error is
        # actually being rendered), so the success path never pays for a fetch it throws away.
        accounts, brokers, list_error = await _list_accounts_and_brokers(
            portfolio_client, user.user_id
        )
        counts = await load_transaction_counts(
            portfolio_client, user.user_id, [a.account_label for a in accounts]
        )
        return {
            "accounts": accounts,
            "brokers": brokers,
            "list_error": list_error,
            "transaction_counts": counts,
        }

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "accounts.html",
        "create_account",
        csrf_token,
        user=user,
        nav_active="accounts",
        extra_context_loader=load_error_context,
    )
    if csrf_error is not None:
        return csrf_error

    if broker == CUSTOM_BROKER:
        broker = custom_broker.strip()
        custom_error = await _custom_broker_error(portfolio_client, broker)
        if custom_error is not None:
            return await _render_form_error(
                request,
                response,
                settings,
                templates,
                "accounts.html",
                custom_error,
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                user=user,
                nav_active="accounts",
                extra_context_loader=load_error_context,
            )

    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="accounts.html",
        action_name="create_account",
        call=lambda: portfolio_client.create_account(
            user_id=user.user_id, broker=broker, account_label=account_label
        ),
        error_messages=_ACCOUNT_ERRORS,
        on_success=lambda _result: _redirect(request, response, "/accounts"),
        user=user,
        nav_active="accounts",
        extra_context_loader=load_error_context,
    )


# --- transactions: list ------------------------------------------------------

_TRANSACTIONS_UNAVAILABLE = (
    "Can't reach the portfolio service right now — the transactions can't be shown."
)


@router.get("/transactions")
async def transactions_page(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    """The account's transaction log, newest first (UI-8). portfolio returns it oldest first
    and takes no paging or sort parameters, so the whole log is fetched and reversed here."""
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    header, transactions = await asyncio.gather(
        _account_header_context(portfolio_client, user.user_id, account),
        portfolio_client.list_transactions(user_id=user.user_id, account_label=account),
        return_exceptions=True,
    )
    if isinstance(header, BaseException):
        raise header
    if isinstance(transactions, AccountNotFound) or header["account_known"] is False:
        return _render_account_not_found(
            request, response, templates, user=user, csrf_token=csrf_token
        )
    error = None
    if isinstance(transactions, PortfolioClientError):
        # None, not []: the template must not read an outage as "no transactions yet".
        logger.exception(
            "portfolio service unavailable listing transactions.", exc_info=transactions
        )
        transactions, error = None, _TRANSACTIONS_UNAVAILABLE
    elif isinstance(transactions, BaseException):
        raise transactions
    # Only a TICKER identifier is a ticker; an ISIN or broker SYMBOL has no tracked name.
    has_tickers = any(t.instrument_identifier_kind == "TICKER" for t in transactions or ())
    return _render(
        request,
        response,
        templates,
        "transactions.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "account_label": account,
            "transactions": None if transactions is None else transactions[::-1],
            "ticker_names": await load_names(portfolio_client) if has_tickers else {},
            "error": error,
            # Set by deletion.py's redirect after a delete: 1 if it deleted the row, 0 if the
            # row was already gone.
            "deleted": _query_int(request, "deleted"),
            "outcome": _write_outcome(request),
            **_account_page_context("transactions"),
            **header,
        },
    )


# --- transactions: manual entry ----------------------------------------------

_TRANSACTION_ERRORS: dict[type[PortfolioClientError], tuple[str, int]] = {
    AccountNotFound: ("That account doesn't exist.", status.HTTP_404_NOT_FOUND),
}


_TICKERS_UNAVAILABLE = (
    "The ticker list couldn't be loaded right now — type the ticker exactly as Stakehold lists it."
)


async def _load_tickers(portfolio_client: PortfolioClient) -> tuple[list[str], str | None]:
    """The tickers the manual-entry picker offers, or an empty list plus a notice when portfolio
    (or data-store behind it) can't be reached. The form stays usable either way: the picker is
    a convenience, and portfolio still checks whatever is typed when the transaction is saved."""
    try:
        return await portfolio_client.list_tickers(), None
    except PortfolioClientError as exc:
        logger.exception("portfolio service unavailable listing tickers.", exc_info=exc)
        return [], _TICKERS_UNAVAILABLE


def _query_int(request: Request, name: str) -> int | None:
    raw = request.query_params.get(name, "")
    return int(raw) if raw.isdigit() else None


def _with_rebuild_statuses(
    url: str, result: TransactionWriteResult | TransactionDeleteResult
) -> str:
    """The post-write (or post-delete) redirect, plus portfolio's follow-on rebuild statuses, so
    the page it lands on can say what they did (UI-7). A status an older portfolio doesn't
    report is left out."""
    statuses = {
        name: value
        for name, value in (
            ("positions", result.positions_rebuild),
            ("returns", result.returns_rebuild),
        )
        if value is not None
    }
    return f"{url}&{urlencode(statuses)}" if statuses else url


def _write_outcome(request: Request) -> str:
    """The rebuild sentence for a confirmation page, from the redirect's query string.
    rebuild_outcome ignores any status it doesn't know, so a hand-edited URL shows nothing."""
    return rebuild_outcome(
        request.query_params.get("positions"), request.query_params.get("returns")
    )


def _utcnow() -> datetime:
    """A seam for tests to fix the form's default date."""
    return datetime.now(timezone.utc)


async def _transaction_new_context(
    portfolio_client: PortfolioClient,
    account_label: str,
    *,
    saved: int | None = None,
    outcome: str = "",
) -> dict:
    tickers, tickers_notice = await _load_tickers(portfolio_client)
    return {
        "account_label": account_label,
        "transaction_types": _TRANSACTION_TYPES,
        # A fresh idempotency key per render: a double-submitted form saves once, while the next
        # form (after the redirect) gets a new one. Editable, so a known broker reference can
        # still be typed in. An error re-render gets a new one too, which is harmless: nothing
        # was saved under the old one.
        "reference": f"manual-{uuid4()}",
        # datetime-local's own format, to the minute; the field is labelled UTC.
        "default_datetime": _utcnow().strftime("%Y-%m-%dT%H:%M"),
        "currencies": _SUGGESTED_CURRENCIES,
        "field_types": _FIELD_TYPES,
        "field_required_for": _FIELD_REQUIRED_FOR,
        "tickers": tickers,
        "tickers_notice": tickers_notice,
        "saved": saved,
        "outcome": outcome,
    }


@router.get("/transactions/new")
async def transaction_new_form(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    header, form = await asyncio.gather(
        _account_header_context(portfolio_client, user.user_id, account),
        _transaction_new_context(
            portfolio_client,
            account,
            saved=_query_int(request, "saved"),
            outcome=_write_outcome(request),
        ),
    )
    if header["account_known"] is False:
        return _render_account_not_found(
            request, response, templates, user=user, csrf_token=csrf_token
        )
    return _render(
        request,
        response,
        templates,
        "transactions_new.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "error": None,
            **_account_page_context("add"),
            **header,
            **form,
        },
    )


def _parse_transaction_form(
    *,
    trade_datetime: str,
    native_amount: str,
    quantity: str,
    price: str,
    fx_rate: str,
    fees_native: str,
) -> dict | None:
    """Turns the manual-entry form's raw strings into the types add_transaction expects, or
    returns None if trade_datetime or any numeric field fails to parse. Everything else --
    which fields a given transaction_type actually requires, EUR-vs-fx_rate, and so on -- is
    portfolio's own validation to enforce; this only has to get the Python types right before
    handing off.
    """
    try:
        parsed_datetime = datetime.fromisoformat(trade_datetime)
        # The shipped form always submits a naive value (an HTML datetime-local input has no
        # timezone), so a missing tzinfo is assumed UTC. A caller that does send an offset (e.g.
        # a non-browser client) is converted rather than having its offset silently discarded.
        if parsed_datetime.tzinfo is None:
            parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
        else:
            parsed_datetime = parsed_datetime.astimezone(timezone.utc)
        # Blank is allowed: portfolio derives a BUY/SELL's amount and reports a missing one
        # for any other type itself.
        parsed_amount = Decimal(native_amount) if native_amount else None
        parsed_quantity = Decimal(quantity) if quantity else None
        parsed_price = Decimal(price) if price else None
        parsed_fx_rate = Decimal(fx_rate) if fx_rate else None
        parsed_fees = Decimal(fees_native) if fees_native else None
    except ValueError, InvalidOperation:
        return None
    for value in (parsed_amount, parsed_quantity, parsed_price, parsed_fx_rate, parsed_fees):
        # Decimal(...) parses "NaN"/"Infinity"/"-Infinity" as valid, non-raising values -- reject
        # them here rather than forwarding a non-finite amount into a financial transaction.
        if value is not None and not value.is_finite():
            return None
    return {
        "trade_datetime": parsed_datetime,
        "native_amount": parsed_amount,
        "quantity": parsed_quantity,
        "price": parsed_price,
        "fx_rate": parsed_fx_rate,
        "fees_native": parsed_fees,
    }


@router.post("/transactions/new")
async def transaction_new_submit(
    request: Request,
    response: Response,
    # default="", not Form(...) -- see accounts_submit's comment above: an empty-string Form
    # value is treated as missing by Form(...), which would bypass CSRF/validation and return a
    # raw 422 instead of this route's own error handling.
    account_label: str = Form(default=""),
    broker_transaction_ref: str = Form(default=""),
    transaction_type: str = Form(default=""),
    trade_datetime: str = Form(default=""),
    native_amount: str = Form(default=""),
    native_currency: str = Form(default=""),
    instrument_identifier: str = Form(default=""),
    quantity: str = Form(default=""),
    price: str = Form(default=""),
    fx_rate: str = Form(default=""),
    fees_native: str = Form(default=""),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    form_context = {
        "user": user,
        **_account_page_context("add"),
        **await _transaction_new_context(portfolio_client, account_label),
    }

    async def load_account_header() -> dict:
        # Lazy, like accounts_submit's loader: only a full-page error re-render shows the header.
        return await _account_header_context(portfolio_client, user.user_id, account_label)

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "transactions_new.html",
        "add_transaction",
        csrf_token,
        extra_context_loader=load_account_header,
        **form_context,
    )
    if csrf_error is not None:
        return csrf_error

    parsed = _parse_transaction_form(
        trade_datetime=trade_datetime,
        native_amount=native_amount,
        quantity=quantity,
        price=price,
        fx_rate=fx_rate,
        fees_native=fees_native,
    )
    if parsed is None:
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            "transactions_new.html",
            "Enter a valid date/time and numeric fields.",
            status.HTTP_400_BAD_REQUEST,
            extra_context_loader=load_account_header,
            **form_context,
        )

    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="transactions_new.html",
        action_name="add_transaction",
        call=lambda: portfolio_client.add_transaction(
            user_id=user.user_id,
            account_label=account_label,
            broker_transaction_ref=broker_transaction_ref,
            transaction_type=transaction_type,
            native_currency=native_currency,
            instrument_identifier=instrument_identifier.strip() or None,
            instrument_identifier_kind=(
                _MANUAL_IDENTIFIER_KIND if instrument_identifier.strip() else None
            ),
            **parsed,
        ),
        error_messages=_TRANSACTION_ERRORS,
        on_success=lambda result: _redirect(
            request,
            response,
            _with_rebuild_statuses(
                f"/transactions/new?account={quote(account_label)}"
                f"&saved={result.transactions_saved}",
                result,
            ),
        ),
        extra_context_loader=load_account_header,
        **form_context,
    )


# --- transactions: broker file import -----------------------------------------

# The import kinds web can send a file to, in the order the importer dropdown lists them, with
# each one's label. Keys are portfolio's GET /brokers `imports` values; a kind portfolio reports
# that isn't here is never offered, since web has no route to send it to.
_IMPORTERS: dict[str, str] = {
    "transactions": "Trades export",
    "cashflow": "Account statement",
}


async def _load_brokers(portfolio_client: PortfolioClient) -> list | None:
    """The installed brokers, or None when portfolio can't list them -- kept distinct from an
    empty list, which means "nothing installed" rather than "unknown"."""
    try:
        return await portfolio_client.list_brokers()
    except PortfolioClientError as exc:
        logger.exception("portfolio service unavailable listing brokers.", exc_info=exc)
        return None


async def _import_page_context(
    portfolio_client: PortfolioClient, user_id: str, account_label: str
) -> dict:
    """The account header plus `importers`: (kind, label) pairs for the importers the account's
    own broker has -- never another broker's, so an account only ever holds its own broker's
    transactions, which broker-scoped ticker mappings rely on. An empty list means the broker
    has none (a custom broker, or a plugin not installed); None means they couldn't be loaded,
    which the page must not present as "none"."""
    header, brokers = await asyncio.gather(
        _account_header_context(portfolio_client, user_id, account_label),
        _load_brokers(portfolio_client),
    )
    account = header["account"]
    if account is None or brokers is None:
        return {**header, "importers": None}
    imports = next((b.imports for b in brokers if b.broker == account.broker), ())
    importers = [(kind, label) for kind, label in _IMPORTERS.items() if kind in imports]
    return {**header, "importers": importers}


@router.get("/transactions/import")
async def transaction_import_form(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    page_context = await _import_page_context(portfolio_client, user.user_id, account)
    if page_context["account_known"] is False:
        return _render_account_not_found(
            request, response, templates, user=user, csrf_token=csrf_token
        )
    return _render(
        request,
        response,
        templates,
        "transactions_import.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "account_label": account,
            "error": None,
            "imported": _query_int(request, "imported"),
            "outcome": _write_outcome(request),
            "importer": None,
            "max_upload_mb": _MAX_UPLOAD_MB,
            **_account_page_context("import"),
            **page_context,
        },
    )


@router.post("/transactions/import")
async def transaction_import_submit(
    request: Request,
    response: Response,
    account_label: str = Form(default=""),
    importer: str = Form(default=""),
    file: UploadFile = File(...),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    form_context = {
        "user": user,
        "account_label": account_label,
        "imported": None,
        "outcome": "",
        "importer": importer,
        "max_upload_mb": _MAX_UPLOAD_MB,
        **_account_page_context("import"),
    }

    async def load_account_header() -> dict:
        return await _import_page_context(portfolio_client, user.user_id, account_label)

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "transactions_import.html",
        "import_transactions",
        csrf_token,
        extra_context_loader=load_account_header,
        **form_context,
    )
    if csrf_error is not None:
        return csrf_error

    # Checked against web's own list only: whether the account's broker has this importer is
    # portfolio's call, and it answers with BrokerParserNotFound (a 422, shown as-is) if not.
    import_calls = {
        "transactions": portfolio_client.import_transactions,
        "cashflow": portfolio_client.import_cashflow,
    }
    if importer not in _IMPORTERS:
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            "transactions_import.html",
            "Choose an importer.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            extra_context_loader=load_account_header,
            **form_context,
        )
    import_call = import_calls[importer]

    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            "transactions_import.html",
            f"That file is too large ({_MAX_UPLOAD_MB} MB limit).",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            extra_context_loader=load_account_header,
            **form_context,
        )

    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"

    return await _submit_portfolio_form(
        request,
        response,
        settings,
        templates,
        template_name="transactions_import.html",
        action_name=f"import ({importer})",
        call=lambda: import_call(
            user_id=user.user_id,
            account_label=account_label,
            filename=filename,
            content=content,
            content_type=content_type,
        ),
        error_messages=_TRANSACTION_ERRORS,
        on_success=lambda result: _redirect(
            request,
            response,
            _with_rebuild_statuses(
                f"/transactions/import?account={quote(account_label)}"
                f"&imported={result.rows_saved}",
                result,
            ),
        ),
        extra_context_loader=load_account_header,
        **form_context,
    )
