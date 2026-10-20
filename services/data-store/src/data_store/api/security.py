"""Shared-secret check for internal service-to-service callers.

This is the trust model `auth/docs/architecture.md` describes: a bearer token proves "a
legitimate internal caller", not a specific caller's identity -- see docs/read-api.md for
that accepted gap. Every route but /health depends on this.
"""

import secrets

from data_store.settings import Settings, load_settings
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


def require_internal_secret(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(load_settings),
) -> None:
    if not settings.internal_api_secret:
        # A deployment that came up without a secret configured is a misconfiguration, not an
        # open endpoint -- fail the request rather than silently accepting every caller.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_API_SECRET is not configured"
        )
    # Compared as UTF-8 bytes, not as str: `compare_digest` refuses a str containing non-ASCII
    # characters, and an Authorization header decodes as latin-1, so a caller sending a
    # non-ASCII token could otherwise turn its own bad credential into a 500.
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), settings.internal_api_secret.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")
