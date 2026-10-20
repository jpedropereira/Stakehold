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

    # No /docs, /redoc or /openapi.json. FastAPI mounts those as plain Starlette routes, so
    # neither the router's bearer dependency nor an app-level one covers them -- they answered
    # 200 to anyone, handing an unauthenticated caller the whole route and parameter surface
    # and contradicting "every route but /health requires Authorization". Serving them behind
    # the bearer instead wouldn't work either: Swagger UI fetches the schema from the browser
    # with no way to attach the secret. This is a service-to-service API whose contract is
    # documented in README.md and docs/read-api.md, so the interactive explorer isn't worth
    # an open endpoint.
    fastapi_app = FastAPI(
        title="data-store read API", docs_url=None, redoc_url=None, openapi_url=None
    )
    fastapi_app.include_router(health_router)
    fastapi_app.include_router(router)
    return fastapi_app
