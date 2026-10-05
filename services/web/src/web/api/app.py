import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from shared.logging_config import configure_logging
from web.api.deps import AdminRequired, AuthRequired
from web.api.htmx import htmx_or_redirect
from web.api.routes import health_router, router
from web.api.security_headers import SecurityHeadersMiddleware
from web.auth_client.http_client import HttpAuthClient
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
            app.state.templates = Jinja2Templates(directory=_TEMPLATES_DIR)
            logger.info(
                "web api: started, auth_api_url=%s, portfolio_api_url=%s.",
                settings.auth_api_url,
                settings.portfolio_api_url,
            )
            yield
        logger.info("web api: http clients closed.")

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()

    app = FastAPI(title="web", lifespan=_lifespan(settings))
    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.mount("/brand", StaticFiles(directory=settings.brand_dir), name="brand")

    app.include_router(health_router)
    app.include_router(router)

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

    return app
