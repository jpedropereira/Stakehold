import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from shared.logging_config import configure_logging
from starlette.exceptions import HTTPException as StarletteHTTPException
from web.api.deletion import router as deletion_router
from web.api.deps import AdminRequired, AuthRequired
from web.api.htmx import htmx_or_redirect
from web.api.overview import router as overview_router
from web.api.portfolio_page import router as portfolio_page_router
from web.api.routes import health_router, router
from web.api.security_headers import SecurityHeadersMiddleware, apply_security_headers
from web.api.theme import router as theme_router
from web.api.theme import theme_context
from web.api.tickers import router as tickers_router
from web.auth_client.http_client import HttpAuthClient
from web.formatting import FILTERS
from web.portfolio_client.http_client import HttpPortfolioClient
from web.settings import Settings, load_settings

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def _lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with (
            httpx.AsyncClient(
                base_url=settings.auth_api_url, timeout=settings.http_timeout_seconds
            ) as auth_http_client,
            httpx.AsyncClient(
                base_url=settings.portfolio_api_url, timeout=settings.http_timeout_seconds
            ) as portfolio_http_client,
        ):
            app.state.settings = settings
            app.state.auth_client = HttpAuthClient(auth_http_client, settings.internal_secret)
            app.state.portfolio_client = HttpPortfolioClient(
                portfolio_http_client, settings.portfolio_internal_secret
            )
            app.state.templates = Jinja2Templates(
                directory=_TEMPLATES_DIR, context_processors=[theme_context]
            )
            app.state.templates.env.filters.update(FILTERS)
            logger.info(
                "web api: started, auth_api_url=%s, portfolio_api_url=%s.",
                settings.auth_api_url,
                settings.portfolio_api_url,
            )
            yield
        logger.info("web api: http clients closed.")

    return lifespan


# The branded error pages (UI-12): (heading, message) for a 404, for any other 4xx, and for an
# unhandled exception. Plain words only (charter chapter IV): what happened, what to do.
_NOT_FOUND = ("Page not found", "There's no page at this address.")
_CLIENT_ERROR = ("Couldn't open that page", "The link or address may be incomplete.")
_SERVER_ERROR = ("Something went wrong", "Stakehold hit an error. Try again in a moment.")


def _render_error_page(
    request: Request, page: tuple[str, str], status_code: int, headers=None
) -> Response:
    """error.html, rendered like forbidden.html: without the user (so no nav, which needs one)
    and without a CSRF token (so no theme toggle). Resolving the session here would cost an
    auth call on every error, and could itself be what failed."""
    heading, message = page
    return request.app.state.templates.TemplateResponse(
        request,
        "error.html",
        {"user": None, "heading": heading, "message": message},
        status_code=status_code,
        headers=headers,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()

    app = FastAPI(title="web", lifespan=_lifespan(settings))
    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.mount("/brand", StaticFiles(directory=settings.brand_dir), name="brand")

    app.include_router(health_router)
    app.include_router(overview_router)
    app.include_router(portfolio_page_router)
    app.include_router(router)
    app.include_router(deletion_router)
    app.include_router(theme_router)
    app.include_router(tickers_router)

    @app.exception_handler(AuthRequired)
    async def _auth_required(request: Request, _exc: AuthRequired) -> Response:
        return htmx_or_redirect(request, "/login")

    @app.exception_handler(AdminRequired)
    async def _admin_required(request: Request, _exc: AdminRequired) -> Response:
        # Rendered without the user: the 403 page has no nav or logout form, so it needs no
        # user or CSRF token -- the visitor gets back via its link to the home page.
        return request.app.state.templates.TemplateResponse(
            request, "forbidden.html", {"user": None}, status_code=status.HTTP_403_FORBIDDEN
        )

    # Starlette's HTTPException, not FastAPI's subclass, so routing's own 404 and 405 land here
    # too. The status and any headers it carries (a 405's Allow) are kept.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        page = _NOT_FOUND if exc.status_code == status.HTTP_404_NOT_FOUND else _CLIENT_ERROR
        return _render_error_page(request, page, exc.status_code, exc.headers)

    # A missing or malformed query or form field: reachable only by a hand-edited URL or a
    # hand-built post, since every link and form in the app supplies its fields.
    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, _exc: RequestValidationError) -> Response:
        return _render_error_page(request, _CLIENT_ERROR, status.HTTP_422_UNPROCESSABLE_CONTENT)

    # Run by ServerErrorMiddleware, which still re-raises afterwards, so the traceback is
    # logged as before. The visitor sees the branded page, never the exception.
    @app.exception_handler(Exception)
    async def _server_error(request: Request, _exc: Exception) -> Response:
        return apply_security_headers(
            _render_error_page(request, _SERVER_ERROR, status.HTTP_500_INTERNAL_SERVER_ERROR)
        )

    return app
