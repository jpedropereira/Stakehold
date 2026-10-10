"""The HTTP entry point -- a new sibling of cli.py, not a replacement for it. Routes are
argument parsing plus a call into jobs.py, the same shape cli.py has. See
docs/flows.md's "HTTP API" section for the full error-mapping/status-code contract this
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
from portfolio.errors import PortfolioError
from portfolio.settings import Settings, SettingsError, load_settings
from shared.logging_config import configure_logging


async def _handle_portfolio_error(_request: Request, exc: PortfolioError) -> JSONResponse:
    # One handler for the whole hierarchy: Starlette resolves the raised exception's own type
    # via its MRO to find a registered handler, so every PortfolioError subclass reaches this
    # one -- its own status_code (errors.py) picks the response, rather than a second table here
    # a new subclass could be added without ever touching.
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


async def _handle_settings_error(_request: Request, exc: SettingsError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


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

    app.add_exception_handler(PortfolioError, _handle_portfolio_error)
    # A job's own load_settings() raising mid-request (e.g. DATASTORE_API_URL missing for
    # import-cashflow/rebuild-returns) -- a misconfigured deployment, not a caller mistake.
    app.add_exception_handler(SettingsError, _handle_settings_error)

    return app
