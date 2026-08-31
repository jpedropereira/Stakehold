import logging
from contextlib import asynccontextmanager

from auth.api.routes import health_router, router
from auth.service import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    RateLimited,
    SessionInvalid,
)
from auth.settings import Settings, load_settings
from auth.throttle import (
    LOGIN_MAX_ATTEMPTS,
    LOGIN_WINDOW_SECONDS,
    SIGNUP_MAX_ATTEMPTS,
    SIGNUP_WINDOW_SECONDS,
    AttemptThrottle,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from shared.db.pool import connection_pool
from shared.logging_config import configure_logging

logger = logging.getLogger(__name__)


def _lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = connection_pool(
            settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
        )
        pool.open(wait=True)
        app.state.pool = pool
        app.state.settings = settings
        # Two limiters, not one: they count different things (login counts failures and
        # resets on success; signup counts every attempt) and must not share a window.
        app.state.login_throttle = AttemptThrottle(
            max_attempts=LOGIN_MAX_ATTEMPTS, window_seconds=LOGIN_WINDOW_SECONDS
        )
        app.state.signup_throttle = AttemptThrottle(
            max_attempts=SIGNUP_MAX_ATTEMPTS, window_seconds=SIGNUP_WINDOW_SECONDS
        )
        logger.info(
            "auth api: started, pool opened (min=%d, max=%d).",
            settings.pool_min_size,
            settings.pool_max_size,
        )
        try:
            yield
        finally:
            pool.close()
            logger.info("auth api: pool closed.")

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()

    app = FastAPI(title="auth", lifespan=_lifespan(settings))
    app.include_router(health_router)
    app.include_router(router)

    # Exception handlers, not per-route try/except, so a bad password, an unknown email, and an
    # expired session all fall through the same generic-401 path without every route repeating it.
    @app.exception_handler(EmailAlreadyRegistered)
    async def _email_taken(_request: Request, _exc: EmailAlreadyRegistered) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "email already registered"})

    @app.exception_handler(InvalidCredentials)
    async def _invalid_credentials(_request: Request, _exc: InvalidCredentials) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": "invalid email or password"})

    @app.exception_handler(SessionInvalid)
    async def _session_invalid(_request: Request, _exc: SessionInvalid) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": "invalid or expired session"})

    @app.exception_handler(RateLimited)
    async def _rate_limited(_request: Request, _exc: RateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"detail": "too many attempts, try again later"}
        )

    return app
