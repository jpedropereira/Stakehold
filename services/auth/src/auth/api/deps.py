import secrets
from collections.abc import Iterator

from auth.settings import Settings
from auth.throttle import LoginThrottle
from fastapi import Depends, Header, HTTPException, Request, status
from psycopg import Connection


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_throttle(request: Request) -> LoginThrottle:
    return request.app.state.throttle


def get_db(request: Request) -> Iterator[Connection]:
    """One pooled connection per request -- checked out here, returned to the pool when the
    request finishes, whether it succeeded or raised."""
    with request.app.state.pool.connection() as conn:
        yield conn


def require_internal_secret(
    settings: Settings = Depends(get_settings),
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
) -> None:
    if x_internal_secret is None or not secrets.compare_digest(
        x_internal_secret, settings.internal_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing internal secret",
        )
