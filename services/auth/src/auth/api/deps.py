import secrets
from collections.abc import Iterator

from auth.settings import Settings
from auth.throttle import AttemptThrottle
from fastapi import Depends, Header, HTTPException, Request, status
from psycopg import Connection

# Long enough for any real address (an IPv6 literal with a zone is under 60 chars) and short
# enough that a forged header can't bloat the throttle map's keys.
_MAX_CLIENT_IP_LENGTH = 64


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_login_throttle(request: Request) -> AttemptThrottle:
    return request.app.state.login_throttle


def get_signup_throttle(request: Request) -> AttemptThrottle:
    return request.app.state.signup_throttle


def get_db(request: Request) -> Iterator[Connection]:
    """One pooled connection per request -- checked out here, returned to the pool when the
    request finishes, whether it succeeded or raised."""
    with request.app.state.pool.connection() as conn:
        yield conn


def client_ip(request: Request) -> str:
    """The end user's address, from X-Forwarded-For when the caller supplied it.

    `request.client.host` is `web`'s container address on every request in the deployed topology
    (`auth` is never reachable from outside the private network), so throttling on it collapses
    every key to its subject alone -- which would let anyone lock a known account out of login by
    guessing wrong five times.

    Trusting the header is sound *here* specifically because every route that uses it sits behind
    the router-level internal-secret dependency: by the time this runs the caller has already
    proved it is `web`, and `web`'s contract is to forward the browser's address. See
    architecture.md, "Login throttling and user enumeration".
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:_MAX_CLIENT_IP_LENGTH]
    return request.client.host if request.client else "unknown"


def require_internal_secret(
    settings: Settings = Depends(get_settings),
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
) -> None:
    # Compared as bytes, not str: Starlette decodes header bytes as latin-1, and
    # secrets.compare_digest raises TypeError for any str character above ASCII -- so a header
    # carrying a raw high byte would turn a rejected caller into a 500.
    if x_internal_secret is None or not secrets.compare_digest(
        x_internal_secret.encode("utf-8"), settings.internal_secret.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing internal secret",
        )
