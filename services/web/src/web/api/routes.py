import logging

from fastapi import APIRouter, Depends, Form, Request, Response, status
from web.api.csrf import CsrfInvalid, ensure_csrf_cookie, verify_csrf
from web.api.deps import (
    client_ip,
    get_auth_client,
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
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()
health_router = APIRouter()

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


def _render_form_error(
    request: Request,
    response: Response,
    settings: Settings,
    templates,
    template_name: str,
    message: str,
    status_code: int,
) -> Response:
    """Re-renders a submitted form with an error, as an htmx partial swap or a full page."""
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
    return _render(
        request,
        response,
        templates,
        template_name,
        {"csrf_token": token, "error": message},
        status_code=status_code,
    )


def _check_csrf(
    request: Request,
    response: Response,
    settings: Settings,
    templates,
    template_name: str,
    action_name: str,
    csrf_token: str,
) -> Response | None:
    """Returns an error Response if the CSRF check fails, otherwise None."""
    try:
        verify_csrf(request, form_token=csrf_token, cookie_secure=settings.cookie_secure)
    except CsrfInvalid:
        logger.warning("%s: CSRF check failed.", action_name)
        return _render_form_error(
            request,
            response,
            settings,
            templates,
            template_name,
            "Something went wrong with that submission. Refresh the page and try again.",
            status.HTTP_400_BAD_REQUEST,
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
        return _render_form_error(
            request, response, settings, templates, template_name, message, status_code
        )

    response.headers.append("set-cookie", result.set_cookie)
    return _redirect(request, response, "/")


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
    csrf_error = _check_csrf(
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
    csrf_error = _check_csrf(
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
