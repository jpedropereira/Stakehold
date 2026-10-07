# Runs the portfolio HTTP API (`portfolio serve`). Built from the repo root, not
# services/portfolio, so uv can resolve the whole workspace lock -- root pyproject.toml depends
# on every service (package = false), so each member's source must be present at `uv sync` time
# even though this image only ever runs portfolio's own entry point. Mirrors
# services/auth/Dockerfile's same-reason multi-COPY layout.
#
# Rebuild after changing services/portfolio/src/portfolio or services/shared/src/shared (docker
# compose build): application code is a build-time snapshot, not mounted live.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_CACHE_DIR=/opt/stakehold/.uv-cache

WORKDIR /opt/stakehold

COPY pyproject.toml uv.lock ./
COPY services/auth services/auth
COPY services/data-store services/data-store
COPY services/portfolio services/portfolio
COPY services/shared services/shared
COPY services/web services/web

RUN uv sync --frozen

# Broker-parser plugins: every wheel dropped into services/portfolio/plugins/ (gitignored, so this
# repo never names one) is installed on top of the locked workspace. No wheels, no-op -- the image
# still builds and serves, and imports fail with BrokerParserNotFound. See plugins/README.md.
RUN if ls services/portfolio/plugins/*.whl >/dev/null 2>&1; then \
        uv pip install services/portfolio/plugins/*.whl; \
    else \
        echo "no broker-parser plugin wheels in services/portfolio/plugins/ -- none installed"; \
    fi

EXPOSE 8002

CMD ["uv", "run", "--frozen", "portfolio", "serve"]
