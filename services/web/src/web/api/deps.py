from fastapi import Depends, Request, Response
from fastapi.templating import Jinja2Templates
from web.auth_client.base import AuthClient, AuthUser
from web.auth_client.errors import AuthClientError
from web.portfolio_client.base import PortfolioClient
from web.settings import Settings


class AuthRequired(Exception):
    """Raised by require_user when the caller has no valid session.

    Mapped by an app-level exception handler to a redirect to /login, rather than every route
    that needs a logged-in user repeating the check.
    """


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_auth_client(request: Request) -> AuthClient:
    return request.app.state.auth_client


def get_portfolio_client(request: Request) -> PortfolioClient:
    return request.app.state.portfolio_client


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def client_ip(request: Request) -> str:
    """The browser's address, from the raw socket only.

    Deliberately does *not* trust an inbound X-Forwarded-For: web is the public edge, so
    honoring a forwarded header here would let any caller spoof the client_ip that auth's
    login/signup throttles key on. When a real reverse proxy sits in front of web, the fix is
    uvicorn's --proxy-headers plus a forwarded_allow_ips allowlist, not trusting this header
    unconditionally.
    """
    return request.client.host if request.client else "unknown"


def _forward_session_cookie(response: Response, set_cookie: str) -> None:
    # auth sets Set-Cookie itself; web proxies that header straight through rather than
    # rebuilding a cookie from a token in the body (see auth/docs/architecture.md, "Endpoint
    # surface"). append(), not set(), since Set-Cookie is a legitimately multi-valued header.
    response.headers.append("set-cookie", set_cookie)


async def _resolve_user(
    request: Request, response: Response, auth_client: AuthClient
) -> AuthUser | None:
    """Validates the browser's session cookie against auth, or returns None on any failure.

    Any failure -- no cookie, an expired/unknown session, or auth being unreachable -- is
    reported the same way. This doesn't distinguish "logged out" from "auth is down" for the
    visitor; that's an accepted gap at this stage (see docs/architecture.md, "Open questions").
    """
    cookie_header = request.headers.get("cookie")
    if cookie_header is None:
        return None

    try:
        result = await auth_client.validate_session(cookie_header=cookie_header)
    except AuthClientError:
        return None

    _forward_session_cookie(response, result.set_cookie)
    return result.user


async def require_user(
    request: Request,
    response: Response,
    auth_client: AuthClient = Depends(get_auth_client),
) -> AuthUser:
    """Same session check as optional_user, but raises AuthRequired instead of returning None."""
    user = await _resolve_user(request, response, auth_client)
    if user is None:
        raise AuthRequired()
    return user


async def optional_user(
    request: Request,
    response: Response,
    auth_client: AuthClient = Depends(get_auth_client),
) -> AuthUser | None:
    """Used where a page renders either way (the home page, or bouncing an already-logged-in
    visitor away from /login and /signup)."""
    return await _resolve_user(request, response, auth_client)
