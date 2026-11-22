# Runs the web HTTP server (`web serve`). Built from the repo root, not services/web, so uv can
# resolve the whole workspace lock -- root pyproject.toml depends on every service (package =
# false), so each member's source must be present at `uv sync` time even though this image only
# ever runs web's own entry point. Mirrors services/auth/Dockerfile's same-reason multi-COPY
# layout.
#
# Rebuild after changing services/web/src/web, services/shared/src/shared, or brand/ (docker
# compose build): application code and brand assets are a build-time snapshot, not mounted live.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_CACHE_DIR=/opt/stakehold/.uv-cache
ENV WEB_BRAND_DIR=/opt/stakehold/brand

WORKDIR /opt/stakehold

COPY pyproject.toml uv.lock ./
COPY services/agent services/agent
COPY services/agent-evals services/agent-evals
COPY services/auth services/auth
COPY services/data-store services/data-store
COPY services/portfolio services/portfolio
COPY services/shared services/shared
COPY services/web services/web
COPY brand brand

RUN uv sync --frozen

EXPOSE 8000

CMD ["uv", "run", "--frozen", "web", "serve"]
