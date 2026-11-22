# Stakehold

Stakehold is a hobby project that helps retail value investors understand their portfolios and
strengthen their weak spots. A typical question it answers: "How concentrated am I, how has this
portfolio really performed, and what would make it more robust?"

It answers by combining three things:

- **Domain knowledge.** Returns, risk measured against a benchmark, concentration and
  look-through exposure, and backtests of "what if I added this position?", computed the way an
  investor would reason about them.
- **Several data sources.** Your own transactions, entered by hand or imported from a broker's
  export file; daily prices and dividends; and the holdings of the ETFs you own, so exposure
  is measured through the funds and not just at the ticker level.
- **AI.** An agent named Pedro (in design, not built yet) will read the same portfolio, risk
  metrics and backtests you see, give a plain evaluation, and suggest where to improve. Later
  it will be able to propose changes on the page, such as a position to backtest, which you
  confirm before anything happens.

Stakehold is built for learning and personal use. It is not financial advice.

## What's in the repo

It's a uv workspace: five services and two library packages.

- `services/data-store/`: the `data_store` package. It scrapes ETF holdings and ingests daily
  prices and dividend events into Postgres, orchestrated by Airflow. See
  [its README](services/data-store/README.md) for setup and the ingestion commands.
- `services/portfolio/`: the `portfolio` package. It records transactions across brokerage
  accounts and derives positions, returns, risk metrics and backtests from them. See
  [its architecture](services/portfolio/docs/architecture.md).
- `services/web/`: the `web` package, the browser UI (FastAPI, Jinja2 and htmx). See
  [its architecture](services/web/docs/architecture.md).
- `services/auth/`: the `auth` package, which owns users, credentials and sessions. See
  [its architecture](services/auth/docs/architecture.md).
- `services/agent/`: the `agent` package, which will host Pedro. Design only so far: see
  [its architecture](services/agent/docs/architecture.md).
- `services/shared/`: a library, not a service. Cross-service Postgres and logging helpers.
- `services/agent-evals/`: a library, not a service. The offline evals for `agent`, kept
  deliberately low-cost. Design only so far: see
  [its architecture](services/agent-evals/docs/architecture.md).

## Development setup

This project uses Python 3.14 and [uv](https://docs.astral.sh/uv/) for dependency
management, with one virtual environment and lockfile shared by every workspace member.

```bash
uv sync --all-extras --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Run the test suite for every service:

```bash
uv run pytest services                 # unit tests
uv run pytest services -m integration  # integration tests (needs Postgres, see below)
```

Tests are split in two. Unit tests are the default: the root `pyproject.toml` sets
`addopts = "-m 'not integration'"`, so a plain `pytest services` deliberately skips
the integration suite and needs nothing running. **A green plain run is therefore not
the whole suite** — the integration tests cover what a fake can't (real `COPY` upserts,
`ON CONFLICT` semantics, schema migration idempotency) and need a live Postgres. Their
fixtures truncate tables, so they run against disposable, in-memory test databases, never
the dev stack:

```bash
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest services -m integration
```

The root `conftest.py` refuses to start an integration run against any other database.

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
locally. The integration step starts the same `docker-compose.test.yml` test
databases a developer does, which is what keeps those tests from being "only run
by whoever remembered to start the database".

## Running the full stack

Each service still comes up independently via its own `services/<service>/docker-compose.yml`.
To run all four together, wired onto a shared Docker network with in-network service discovery:

```bash
./scripts/stack.sh up      # create the network if missing, start all four, wait for /health
./scripts/stack.sh ps      # status of every service's containers
./scripts/stack.sh health  # one-shot check of all four /health endpoints
./scripts/stack.sh down    # stop all four, in reverse order
```

`up` still expects each service's real `.env` to already exist with matching shared secrets
across services — that's your own step, nothing automates it (see below). Pass extra args
through to `up`, e.g. `./scripts/stack.sh up --build` to rebuild images first.

See [`docs/smoke-test.md`](docs/smoke-test.md) for what the script wraps, the one-time
`docker network create` prerequisite, how to populate each service's `.env` with matching
shared secrets, and the full register → login → add account → rebuild → view walk-through.
