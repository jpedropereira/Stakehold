"""The visitor's light/dark/system choice (UI-3).

The choice lives in a cookie and is applied on the server: base.html renders `data-theme` on
<html> from it, so a manual choice shows on the first paint with no inline script and no flash
of the wrong theme. "system" renders no attribute and leaves the choice to web.css's
`prefers-color-scheme` media query. See docs/architecture.md, "Theme".
"""

import json
import logging

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from web.api.csrf import CsrfInvalid, verify_csrf
from web.api.deps import get_settings
from web.api.htmx import htmx_or_redirect, is_htmx
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

THEMES = ("light", "dark", "system")
DEFAULT_THEME = "system"
COOKIE_NAME = "stakehold_theme"
_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
# Fired on the toggle form by htmx (HX-Trigger), handled by static/theme.js.
_THEME_EVENT = "stakehold:theme"


def current_theme(request: Request) -> str:
    """The cookie's theme, or the default for a missing or unrecognised value: the cookie comes
    from the browser, and an arbitrary value must never reach the rendered attribute."""
    value = request.cookies.get(COOKIE_NAME)
    return value if value in THEMES else DEFAULT_THEME


def theme_context(request: Request) -> dict:
    """Jinja2Templates context processor (registered in app.py), so every template gets
    `theme` -- including pages rendered outside routes.py, like the 403 page -- without each
    route passing it."""
    return {"theme": current_theme(request)}


def safe_next(value: str) -> str:
    """`value` if it's a path on this site, else "/". It becomes a redirect Location, so
    anything that could leave the site is refused: a scheme or a protocol-relative "//host",
    "/\\host" (which browsers read as "//host"), and any control character or whitespace
    (browsers drop tabs and newlines, so "/\\t/host" also becomes "//host"). The toggle only
    ever sends request.url's path and query, which are already percent-encoded ASCII, so
    rejecting everything outside printable ASCII costs nothing."""
    if not value.startswith("/") or value[1:2] in ("/", "\\"):
        return "/"
    if not all(0x21 <= ord(c) <= 0x7E for c in value):
        return "/"
    return value


@router.post("/theme")
async def theme_submit(
    request: Request,
    theme: str = Form(default=""),
    next_url: str = Form(default="/", alias="next"),
    csrf_token: str = Form(default=""),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Saves the choice and returns to the page it was made on. Needs no login: the theme is a
    per-browser preference, and the login page has the toggle too.

    A plain form post gets a 303 back to `next`. An htmx one gets a 200 with an HX-Trigger
    instead, and static/theme.js swaps the theme in place, without a reload."""
    target = safe_next(next_url)
    try:
        verify_csrf(request, form_token=csrf_token, cookie_secure=settings.cookie_secure)
    except CsrfInvalid:
        # Same handling as logout: nothing worth showing an error for, so go back unchanged.
        # For htmx that's a full navigation, which also reissues a stale CSRF token.
        logger.warning("theme: CSRF check failed.")
        return htmx_or_redirect(request, target)

    if theme not in THEMES:
        return Response("Unknown theme.", status_code=status.HTTP_400_BAD_REQUEST)

    if is_htmx(request):
        response = Response(
            status_code=status.HTTP_200_OK,
            headers={"HX-Trigger": json.dumps({_THEME_EVENT: theme})},
        )
    else:
        response = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    # HttpOnly: only the server reads it. JS learns the theme from <html data-theme>, or from
    # the HX-Trigger above.
    response.set_cookie(
        key=COOKIE_NAME,
        value=theme,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response
