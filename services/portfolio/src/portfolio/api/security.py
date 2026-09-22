"""Shared-secret check for internal service-to-service callers.

X-Internal-Secret, matching auth's inbound convention (auth/api/deps.py) rather than
data-store's Authorization: Bearer -- so web's future PortfolioClient sends the same header its
existing HttpAuthClient already does. Deliberately a separate credential from the
INTERNAL_API_SECRET portfolio itself sends outbound to data-store (settings.py's
api_internal_secret vs internal_api_secret): the two rotate independently.

Settings come from app.state (set once in create_app()), not a fresh load_settings() per
request -- auth's shape, not data-store's -- because create_app() takes an injectable `settings`
argument precisely so tests can build an app with a known secret without env vars.
"""

import secrets

from fastapi import Depends, Header, HTTPException, Request, status
from portfolio.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_internal_secret(
    settings: Settings = Depends(get_settings),
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
) -> None:
    if not settings.api_internal_secret:
        # A deployment that came up without PORTFOLIO_INTERNAL_SECRET set is a
        # misconfiguration, not an open endpoint -- fail the request rather than silently
        # accepting every caller. create_app() also checks this at startup; this covers a
        # settings object built after startup (e.g. a reload) slipping through.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "PORTFOLIO_INTERNAL_SECRET is not configured"
        )
    # Compared as UTF-8 bytes, not str: Starlette decodes header bytes as latin-1, and
    # secrets.compare_digest raises TypeError for any str character above ASCII -- so a header
    # carrying a raw high byte would turn a rejected caller into a 500 instead of a 401.
    if x_internal_secret is None or not secrets.compare_digest(
        x_internal_secret.encode("utf-8"), settings.api_internal_secret.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing internal secret")
