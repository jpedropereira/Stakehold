import logging

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from web.api.csrf import CsrfInvalid, ensure_csrf_cookie, verify_csrf
from web.api.deps import (
    client_ip,
    get_auth_client,
    get_settings,
    get_templates,
    optional_user,
    require_user,
)
from web.auth_client.base import AuthClient, AuthUser
from web.auth_client.errors import (
    AuthUnavailable,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRequest,
    RateLimited,
)
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()
health_router = APIRouter()

# Must match auth/api/cookies.py's COOKIE_NAME. Not imported directly: web and auth share no
# code beyond the `shared` infrastructure package -- they communicate only through the HTTP API,
# and the cookie's name is part of that contract, same as its own documented endpoint surface.
_SESSION_COOKIE_NAME = "stakehold_session"


@health_router.get("/health")
def health() -> dict[str, str]:
    # Deliberately doesn't probe auth: web is up whether or not auth is, and a dependent
    # healthcheck would make a transient auth blip restart web too. See docs/architecture.md.
    return {"status": "ok"}


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") is not None


def _finish(sub_response: Response, built: Response) -> Response:
    """Copies headers set on the dependency-injected `response` param onto the Response this
    route actually returns.

    FastAPI only auto-merges the injected sub-response's headers when a route returns a plain
    value it serializes itself; a route (like every one here) that returns a Response subclass
    directly -- TemplateResponse, RedirectResponse -- is used as-is, with no merge. Without this,
    a cookie set via `response.set_cookie`/`ensure_csrf_cookie`/`response.headers.append`
    earlier in the same request would be silently dropped.
    """
    built.headers.raw.extend(sub_response.headers.raw)
    return built


def _redirect(request: Request, response: Response, url: str) -> Response:
    """A 303 for a plain form post, or an HX-Redirect for an htmx one.

    htmx's own XHR follows a 303 transparently and swaps the target's *content* in place --
    which would drop a full HTML page into a form's error slot. HX-Redirect instead tells htmx
    to perform a real browser-level navigation, matching what a plain POST's redirect achieves.
    """
    if _is_htmx(request):
        built = Response(status_code=status.HTTP_200_OK, headers={"HX-Redirect": url})
    else:
        built = RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    return _finish(response, built)


@router.get("/")
def home(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
):
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    built = templates.TemplateResponse(
        request, "home.html", {"user": user, "csrf_token": csrf_token}
    )
    return _finish(response, built)


@router.get("/login")
def login_form(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser | None = Depends(optional_user),
):
    if user is not None:
        return _redirect(request, response, "/")
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    built = templates.TemplateResponse(
        request, "login.html", {"csrf_token": csrf_token, "error": None}
    )
    return _finish(response, built)


@router.post("/login")
def login_submit(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    auth_client: AuthClient = Depends(get_auth_client),
):
    def _render_error(message: str, status_code: int) -> Response:
        if _is_htmx(request):
            built = templates.TemplateResponse(
                request,
                "partials/_form_errors.html",
                {"error": message},
                status_code=status_code,
            )
            return _finish(response, built)
        token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
        built = templates.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": token, "error": message},
            status_code=status_code,
        )
        return _finish(response, built)

    try:
        verify_csrf(request, form_token=csrf_token, cookie_secure=settings.cookie_secure)
    except CsrfInvalid:
        logger.warning("login: CSRF check failed.")
        return _render_error(
            "Something went wrong with that submission. Refresh the page and try again.",
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = auth_client.login(email=email, password=password, client_ip=client_ip(request))
    except InvalidCredentials:
        return _render_error("That email and password don't match.", status.HTTP_401_UNAUTHORIZED)
    except RateLimited:
        return _render_error(
            "Too many attempts. Try again in a few minutes.", status.HTTP_429_TOO_MANY_REQUESTS
        )
    except InvalidRequest:
        return _render_error(
            "Check your email and password and try again.", status.HTTP_400_BAD_REQUEST
        )
    except AuthUnavailable:
        logger.exception("login: auth service unavailable.")
        return _render_error(
            "Can't reach the sign-in service right now.", status.HTTP_503_SERVICE_UNAVAILABLE
        )

    response.headers.append("set-cookie", result.set_cookie)
    return _redirect(request, response, "/")


@router.get("/signup")
def signup_form(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser | None = Depends(optional_user),
):
    if user is not None:
        return _redirect(request, response, "/")
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
    built = templates.TemplateResponse(
        request, "signup.html", {"csrf_token": csrf_token, "error": None}
    )
    return _finish(response, built)


@router.post("/signup")
def signup_submit(
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
    def _render_error(message: str, status_code: int) -> Response:
        if _is_htmx(request):
            built = templates.TemplateResponse(
                request,
                "partials/_form_errors.html",
                {"error": message},
                status_code=status_code,
            )
            return _finish(response, built)
        token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)
        built = templates.TemplateResponse(
            request,
            "signup.html",
            {"csrf_token": token, "error": message},
            status_code=status_code,
        )
        return _finish(response, built)

    try:
        verify_csrf(request, form_token=csrf_token, cookie_secure=settings.cookie_secure)
    except CsrfInvalid:
        logger.warning("signup: CSRF check failed.")
        return _render_error(
            "Something went wrong with that submission. Refresh the page and try again.",
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = auth_client.signup(
            email=email,
            password=password,
            display_name=display_name,
            client_ip=client_ip(request),
        )
    except EmailAlreadyRegistered:
        return _render_error("That email is already registered.", status.HTTP_409_CONFLICT)
    except RateLimited:
        return _render_error(
            "Too many attempts. Try again in a few minutes.", status.HTTP_429_TOO_MANY_REQUESTS
        )
    except InvalidRequest:
        return _render_error(
            "Check your email, password, and name, and try again.", status.HTTP_400_BAD_REQUEST
        )
    except AuthUnavailable:
        logger.exception("signup: auth service unavailable.")
        return _render_error(
            "Can't reach the sign-in service right now.", status.HTTP_503_SERVICE_UNAVAILABLE
        )

    response.headers.append("set-cookie", result.set_cookie)
    return _redirect(request, response, "/")


@router.post("/logout")
def logout_submit(
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
            auth_client.logout(cookie_header=cookie_header)
        except AuthUnavailable:
            # Logging out is a one-way door for the user's intent: even if auth couldn't be
            # reached, clear the cookie locally so the browser stops sending a stale session.
            logger.exception("logout: auth service unavailable.")

    response.delete_cookie(key=_SESSION_COOKIE_NAME, path="/")
    return _redirect(request, response, "/login")
