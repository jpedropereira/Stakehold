"""The HTTP entry point -- a new sibling of cli.py and dags/, not a replacement for either.

`create_app()` fails loudly if `INTERNAL_API_SECRET` is unset, rather than deferring to the
first request: a deployment that came up misconfigured should not silently serve 500s until
someone happens to call an endpoint. It's a factory rather than a module-level `app` object on
purpose -- building it has a side effect (the settings check), so importing this module (e.g. to
reuse `create_app` in a test) must not itself require the environment to be fully configured.
Run with:

    uv run uvicorn data_store.api.app:create_app --factory --host 0.0.0.0 --port 8000
"""

from data_store.api.routes import health_router, router
from data_store.settings import SettingsError, load_settings
from fastapi import FastAPI
from shared.logging_config import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    settings = load_settings()
    if not settings.internal_api_secret:
        raise SettingsError("INTERNAL_API_SECRET is not set")

    fastapi_app = FastAPI(title="data-store read API")
    fastapi_app.include_router(health_router)
    fastapi_app.include_router(router)
    return fastapi_app
