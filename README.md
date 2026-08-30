# Stakehold

Stakehold is an application to manage different financial portfolios, calculate
their returns, and identify possible improvements.

It's a uv workspace containing four services:

- `services/data-store/` — the `data_store` package: scrapes ETF holdings, ingests daily prices
  and dividend events into Postgres, orchestrated by Airflow. See
  [its README](services/data-store/README.md) for setup and the ingestion commands.
- `services/portfolio/` — the `portfolio` package (scaffolding; no business logic yet)
- `services/web/` — the `web` package (scaffolding; no business logic yet)
- `services/auth/` — the `auth` package (scaffolding; no business logic yet)

## Development setup

This project uses Python 3.14 and [uv](https://docs.astral.sh/uv/) for dependency
management, with a shared virtual environment and lockfile across both services.

```bash
uv sync --all-extras --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Run the test suite for both services:

```bash
uv run pytest services                 # unit tests
uv run pytest services -m integration  # integration tests (needs Postgres, see below)
```

Tests are split in two. Unit tests are the default: the root `pyproject.toml` sets
`addopts = "-m 'not integration'"`, so a plain `pytest services` deliberately skips
the integration suite and needs nothing running. **A green plain run is therefore not
the whole suite** — the integration tests cover what a fake can't (real `COPY` upserts,
`ON CONFLICT` semantics, schema migration idempotency) and need a live Postgres:

```bash
cd services/data-store && docker compose up -d postgres
cd ../.. && uv run pytest services -m integration
```

Commits run a fast, testmon-scoped subset of tests plus formatting/lint/secret
checks; pushes run the full unit suite. See `.pre-commit-config.yaml` for the
full hook chain and the reasoning behind it.

### Secret scanning

`detect-secrets` runs on every commit against `.secrets.baseline`, which
records the secrets already known/audited in the repo so only *new* ones get
flagged. If it blocks a commit, either remove the secret or, if it's a false
positive, mark it in the baseline:

```bash
uv run detect-secrets audit .secrets.baseline
```

### CI

`.github/workflows/ci.yml` runs on every push to `main` and on pull requests.
It runs the exact same `.pre-commit-config.yaml` hook chain via
`pre-commit/action`, then both test suites — so CI can't drift from what runs
locally. The integration step gets its Postgres from a service container, which
is what keeps those tests from being "only run by whoever remembered to start
the database".
