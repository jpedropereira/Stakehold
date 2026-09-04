"""The HTTP entry point -- a new sibling of cli.py, not a replacement for it. Routes are
argument parsing plus a call into jobs.py, the same shape cli.py has. See
docs/architecture.md's "HTTP API" section for the full error-mapping/status-code contract this
module implements.

create_app() fails loudly if PORTFOLIO_INTERNAL_SECRET is unset, rather than deferring to the
first request -- matching data-store's own create_app(): a deployment that came up
misconfigured should not silently serve 500s until someone happens to call an endpoint.

No lifespan, no connection pool: each job call opens and closes its own connection, exactly as
it already does for the CLI (jobs.py's own module docstring) -- a known limitation, documented
alongside psycopg_pool as the intended fix if request volume ever makes it a bottleneck.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from portfolio.api.routes import health_router, router
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    BrokerFileError,
    BrokerParserNotFound,
    BrokerPluginError,
    DataStoreApiError,
    InvalidBroker,
    InvalidTransaction,
    PortfolioError,
    TickerMappingError,
)
from portfolio.settings import Settings, SettingsError, load_settings
from shared.logging_config import configure_logging

# Ordered narrowest-first only for readability -- Starlette's ExceptionMiddleware resolves a
# raised exception's own type before ever consulting its MRO, so registration order here has no
# effect on which handler an exception actually reaches; it always gets its exact match if one
# is registered.
_STATUS_BY_ERROR: tuple[tuple[type[PortfolioError], int], ...] = (
    (AccountNotFound, 404),
    (AccountLabelConflict, 409),
    (InvalidBroker, 422),
    (InvalidTransaction, 422),
    (BrokerFileError, 422),
    (BrokerParserNotFound, 422),
    (TickerMappingError, 500),
    (BrokerPluginError, 500),
    (DataStoreApiError, 502),
)


def _handler_for(status_code: int):
    async def handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()
    if not settings.api_internal_secret:
        raise SettingsError("PORTFOLIO_INTERNAL_SECRET is not set")

    # No /docs, /redoc or /openapi.json -- same reasoning as data-store's create_app(): those
    # mount as plain Starlette routes no router dependency covers, so they'd answer 200 to
    # anyone. This is a service-to-service API documented in docs/architecture.md, not one with
    # an interactive audience.
    app = FastAPI(title="portfolio", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(router)

    for exc_type, status_code in _STATUS_BY_ERROR:
        app.add_exception_handler(exc_type, _handler_for(status_code))
    # Catches any PortfolioError subclass not named above -- fails closed as a 500 rather than
    # an unhandled exception reaching the caller as a raw traceback.
    app.add_exception_handler(PortfolioError, _handler_for(500))
    # A job's own load_settings() raising mid-request (e.g. DATASTORE_API_URL missing for
    # import-cashflow/rebuild-returns) -- a misconfigured deployment, not a caller mistake.
    app.add_exception_handler(SettingsError, _handler_for(500))

    return app
