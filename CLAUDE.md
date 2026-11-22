# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Stakehold is an application to manage financial portfolios, calculate their returns, and identify
possible improvements. It's a uv workspace containing five services plus two library packages:

- `services/data-store/` — the `data_store` package. Implemented: scrapes iShares ETF holdings
  (Playwright + CSV), ingests daily OHLCV and dividend events from yfinance into Postgres, exposes
  a `data-store` CLI, and is orchestrated by Airflow DAGs running in a custom image. Has its own
  scoped rules under `services/data-store/.claude/rules/`.
- `services/portfolio/` — the `portfolio` package. Implemented: records transactions across
  brokerage accounts per user, derives positions, returns and risk figures, and serves them over an
  HTTP API and a CLI. See [`services/portfolio/docs/architecture.md`](services/portfolio/docs/architecture.md).
- `services/web/` — the `web` package. Implemented: the user-facing FastAPI + Jinja2 + htmx app
  (auth pages, portfolio entry and view). It owns no data and calls `portfolio`, `data-store` and
  `auth` on the user's behalf. See [`services/web/docs/architecture.md`](services/web/docs/architecture.md).
- `services/auth/` — the `auth` package. Implemented: Postgres schema (`users`, `sessions`), an
  `auth db init` CLI, and the identity HTTP API (signup, login, logout, session validation).
  See [`services/auth/docs/architecture.md`](services/auth/docs/architecture.md).
- `services/shared/` — the `shared` package. Not a service: no entry point, no schema, no business
  logic. Cross-service infrastructure only — a Postgres connection/transaction/schema-apply helper
  and log setup — that `data-store`, `auth`, `portfolio` and `web` depend on instead of each keeping
  its own copy.
  See [`services/shared/docs/architecture.md`](services/shared/docs/architecture.md).
- `services/agent/` — the `agent` package, which hosts the AI agent Pedro (chat-based portfolio
  evaluation). Design only, empty skeleton: see
  [`services/agent/docs/architecture.md`](services/agent/docs/architecture.md). Has its own scoped
  rules under `services/agent/.claude/rules/` and its own `services/agent/CLAUDE.md`.
- `services/agent-evals/` — the `agent_evals` package. Not a service: the offline eval harness
  for `agent` (depends on `agent`, never the reverse). Design only, empty skeleton: see
  [`services/agent-evals/docs/architecture.md`](services/agent-evals/docs/architecture.md). Has its
  own scoped rules under `services/agent-evals/.claude/rules/` and its own
  `services/agent-evals/CLAUDE.md`.

They share one virtual environment and one lockfile (`uv.lock`) at the workspace root. Python 3.14 is
required.

## Commands

### Setup

```bash
uv sync --all-extras --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

### Tests

Tests are split by marker. `pytest` defaults to unit tests only — the root `pyproject.toml` sets
`addopts = "-m 'not integration'"` — so a plain run skips the integration suite entirely and needs
nothing running. Don't treat a green plain run as "the suite passed".

```bash
uv run pytest services                                                        # unit tests, all services
uv run pytest services -m integration                                         # integration tests (needs Postgres, below)
uv run pytest services/portfolio                                              # one service
uv run pytest services/portfolio/tests/unit/test_cli.py::test_db_init_prints_the_table_count_to_stdout  # single test
uv run pytest services --testmon --tb=short -x                                # fast, testmon-scoped (what pre-commit runs on commit)
```

Integration tests hit a real Postgres (they cover `COPY` upserts, `ON CONFLICT` behaviour and
schema-migration idempotency — the parts a fake can't cover). Their fixtures **truncate tables**,
so they run only against the disposable test databases in `docker-compose.test.yml`. Those are
in memory, on ports 5543x, and can run alongside the dev stack:

```bash
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest services -m integration
```

**Never point integration tests at the dev stack's databases.** That twice wiped real users and
portfolio data. The root `conftest.py` enforces it: it supplies the test databases' URLs, and it
aborts any run that selects an integration test unless every `*DATABASE_URL` names a `*_test`
database on a server started with `stakehold.purpose=integration-test`. It also turns off dotenv
loading for the run, so no service's dev settings can leak in. Never add a `load_dotenv` call or
a fallback database URL to a test.

**Never add an `__init__.py` under a service's `tests/`.** Tests run with
`--import-mode=importlib`, which names each test module after its package path. When several
services had `tests/unit/__init__.py`, all of them were the package `unit`, so portfolio's
`test_cli.py` and auth's were the same module `unit.test_cli`: pytest silently ran auth's tests
in place of portfolio's, and about 145 tests never ran in a workspace run. Without packages each
module is named after its own path. The root `conftest.py` stops any run where a test file's
tests came from a different file. A consequence: test files can't import shared helpers from a
sibling module. Share fixtures through a `conftest.py`, or repeat small helpers per file.

### Lint/format

```bash
uv run autoflake --in-place --remove-all-unused-imports --remove-unused-variable -r .
uv run black --line-length=100 .
uv run pycln .
uv run isort --profile black --filter-files .
uv run flake8
```

### Secret scanning

`detect-secrets` runs on every commit against `.secrets.baseline`. If it blocks a commit on a false
positive, mark it audited:

```bash
uv run detect-secrets audit .secrets.baseline
```

## Architecture

`services/data-store/docs/architecture.md` covers that service's own layering, module
boundaries, extension points and deployment topology — read it before changing anything
structural there. What follows is workspace-level only.

- **uv workspace, not independent projects**: root `pyproject.toml` sets `package = false` and
  `[tool.uv.workspace] members = ["services/*"]`; every workspace package is listed as a dependency
  of the root package. This is why there's a single `.venv` and `uv.lock` instead of one per service.
- **Hook chain order is deliberate** (`.pre-commit-config.yaml`): autoflake → black → pycln → isort
  run first because they mutate files; flake8 runs last so it lints the final, formatted result.
  Preserve this order when editing the config.
- **flake8 config lives only in `.flake8`**, not duplicated as hook `args` — a prior project had
  `.flake8` and a `pyproject.toml` `[tool.flake8]` section drift apart, so this repo keeps a single
  source of truth.
- **Two-tier test gate**: `pre-commit` stage runs `pytest --testmon -x` (only tests touched by the
  change, so commits stay fast); `pre-push` stage runs the full suite. CI (`.github/workflows/ci.yml`)
  then runs the exact same `.pre-commit-config.yaml` (via the lockfile-pinned `uv run pre-commit`,
  skipping only `pytest-fast`), followed by the full unit and integration suites — so local hooks
  and CI can never disagree about what "passing" means. `pytest-fast` is skipped because CI has no
  testmon data, so it would just rerun the whole unit suite a second time.
- **detect-secrets runs on every commit**, not just push, against `.secrets.baseline`, which records
  already-audited findings so only genuinely new secrets get flagged.

## Working practices

Adapted from [AI Blindspots](https://ezyang.github.io/ai-blindspots/) by ezyang. Debugging method,
security boundaries, testing philosophy, observability/logging, data-schema ownership,
architecture documentation, cross-service task tracking, and context/session management live in
`.claude/rules/*.md` and apply automatically — some rules are service-scoped (e.g.
`services/data-store/.claude/rules/`), layered on top of the root-level ones. The following aren't
covered there:

- **Stop digging**: after 3 failed attempts at the same approach, stop and propose an alternative
  instead of persisting.
- **Specify requirements, not solutions**: when a requirement is unspecified, ask or state the
  assumption rather than silently picking a default.
- **Communicate uncertainty**: for non-trivial recommendations, state a confidence level and name
  the alternatives and trade-offs considered.
- **Decompose changes**: break work into focused, single-responsibility, reviewable steps. Do
  preparatory refactoring first (make the change easy), then make the easy change. Get a walking
  skeleton (basic end-to-end version) working before optimizing.
- **Know the limits**: lean on AI strengths (boilerplate, mechanical refactoring, code generation)
  but treat debugging non-obvious bugs, root-cause calls, and security implications as needing extra
  verification — LLMs sound confident when wrong. Verify library/API behavior against primary docs
  rather than relying on generated claims.
- **Stateless commands**: each terminal command should be independently runnable and not depend on
  prior shell state (e.g. an earlier `cd`).


## Brand

Stakehold's visual identity is defined in `brand/brand-charter.html`. Design
tokens are in `brand/tokens.css` — import it once and use the CSS custom
properties, don't hardcode hex values.

- Colors: 5 total. `--stone` (ink/dark surfaces), `--parchment` (background),
  `--gold` (the ONLY accent — buttons, highlights, the logo). `--forest` and
  `--oxblood` are reserved strictly for gains and losses in figures — never
  used decoratively.
- Type: `--font-display` (Fraunces) for headlines and emphasis only,
  `--font-body` (Inter) for everything read in bulk, `--font-mono`
  (IBM Plex Mono) for every number — prices, tickers, percentage deltas.
- Logo: use `brand/logos/logo-gold.svg` on dark/stone surfaces,
  `brand/logos/logo-stone.svg` on light/parchment surfaces, and
  `brand/logos/app-icon.svg` for the favicon/home-screen icon. Never
  recolor, stretch, or add a shadow to the mark.
- Voice: plain verbs, no hype on gains, no panic on losses, numbers do the
  talking. See chapter IV of the brand charter for the full principles.

When building a new component, check the brand charter's relevant chapter
first (I: mark, II: palette, III: type, IV: voice, V: applied example)
rather than guessing.
