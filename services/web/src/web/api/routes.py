import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

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
from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidPortfolioRequest,
    PortfolioClientError,
)
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()
health_router = APIRouter()

# Mirrors portfolio/transactions/base.py's TRANSACTION_TYPES/IDENTIFIER_KINDS -- not imported
# directly, since web talks to portfolio only over HTTP (it has no Python dependency on the
# portfolio package). Kept here as the one place this contract is duplicated, the same way
# _SESSION_COOKIE_NAME below documents auth's cookie name as part of the HTTP contract rather
# than a shared import.
_TRANSACTION_TYPES = ("BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FX", "FEE", "ADJUSTMENT")
_IDENTIFIER_KINDS = ("ISIN", "SYMBOL")

# Matches portfolio's own cap (portfolio/api/routes.py's _MAX_UPLOAD_BYTES) -- read here, before
# the bytes are ever forwarded, so an oversized upload doesn't sit fully buffered in web's memory
# only to be rejected one hop later.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024

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
        return _render(
            request,
            response,
            templates,
            "partials/_form_errors.html",
            {"error": message},
            status_code=status_code,
        )
    token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    context = {"csrf_token": token, "error": message, **extra_context}
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


@router.get("/")
async def home(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    return _render(
        request, response, templates, "home.html", {"user": user, "csrf_token": csrf_token}
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
            "accounts": accounts,
            "brokers": brokers,
            "error": list_error,
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
        accounts, brokers, _list_error = await _list_accounts_and_brokers(
            portfolio_client, user.user_id
        )
        return {"accounts": accounts, "brokers": brokers}

    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "accounts.html",
        "create_account",
        csrf_token,
        user=user,
        extra_context_loader=load_error_context,
    )
    if csrf_error is not None:
        return csrf_error

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
        extra_context_loader=load_error_context,
    )


# --- transactions: manual entry ----------------------------------------------

_TRANSACTION_ERRORS: dict[type[PortfolioClientError], tuple[str, int]] = {
    AccountNotFound: ("That account doesn't exist.", status.HTTP_404_NOT_FOUND),
}


def _transaction_new_context(account_label: str, *, saved: bool) -> dict:
    return {
        "account_label": account_label,
        "transaction_types": _TRANSACTION_TYPES,
        "identifier_kinds": _IDENTIFIER_KINDS,
        "saved": saved,
    }


@router.get("/transactions/new")
async def transaction_new_form(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    saved = request.query_params.get("saved") == "1"
    return _render(
        request,
        response,
        templates,
        "transactions_new.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "error": None,
            **_transaction_new_context(account, saved=saved),
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
        parsed_amount = Decimal(native_amount)
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
    instrument_identifier_kind: str = Form(default=""),
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
    form_context = {"user": user, **_transaction_new_context(account_label, saved=False)}
    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "transactions_new.html",
        "add_transaction",
        csrf_token,
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
            instrument_identifier=instrument_identifier or None,
            instrument_identifier_kind=instrument_identifier_kind or None,
            **parsed,
        ),
        error_messages=_TRANSACTION_ERRORS,
        on_success=lambda _result: _redirect(
            request, response, f"/transactions/new?account={quote(account_label)}&saved=1"
        ),
        **form_context,
    )


# --- transactions: broker file import -----------------------------------------


@router.get("/transactions/import")
async def transaction_import_form(
    request: Request,
    response: Response,
    account: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    imported_raw = request.query_params.get("imported")
    imported = int(imported_raw) if imported_raw and imported_raw.isdigit() else None
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
            "imported": imported,
        },
    )


@router.post("/transactions/import")
async def transaction_import_submit(
    request: Request,
    response: Response,
    account_label: str = Form(default=""),
    file: UploadFile = File(...),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
):
    form_context = {"user": user, "account_label": account_label, "imported": None}
    csrf_error = await _check_csrf(
        request,
        response,
        settings,
        templates,
        "transactions_import.html",
        "import_transactions",
        csrf_token,
        **form_context,
    )
    if csrf_error is not None:
        return csrf_error

    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        return await _render_form_error(
            request,
            response,
            settings,
            templates,
            "transactions_import.html",
            "That file is too large (20MB limit).",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
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
        action_name="import_transactions",
        call=lambda: portfolio_client.import_transactions(
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
            f"/transactions/import?account={quote(account_label)}&imported={result.rows_saved}",
        ),
        **form_context,
    )
