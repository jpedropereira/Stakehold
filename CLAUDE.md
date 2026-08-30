# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Stakehold is an application to manage financial portfolios, calculate their returns, and identify
possible improvements. It's a uv workspace containing four services:

- `services/data-store/` — the `data_store` package. Implemented: scrapes iShares ETF holdings
  (Playwright + CSV), ingests daily OHLCV and dividend events from yfinance into Postgres, exposes
  a `data-store` CLI, and is orchestrated by Airflow DAGs running in a custom image. Has its own
  scoped rules under `services/data-store/.claude/rules/`.
- `services/portfolio/` — the `portfolio` package. Still empty scaffolding, no business logic yet.
- `services/web/` — the `web` package. Empty scaffolding, no business logic yet.
- `services/auth/` — the `auth` package. Empty scaffolding, no business logic yet.

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
uv run pytest services                                                        # unit tests, both services
uv run pytest services -m integration                                         # integration tests (needs Postgres, below)
uv run pytest services/portfolio                                              # one service
uv run pytest services/portfolio/tests/test_placeholder.py::test_placeholder  # single test
uv run pytest services --testmon --tb=short -x                                # fast, testmon-scoped (what pre-commit runs on commit)
```

Integration tests hit a real Postgres (they cover `COPY` upserts, `ON CONFLICT` behaviour and
schema-migration idempotency — the parts a fake can't cover). They read `DATABASE_URL` and create
their own schema, so an empty database is fine:

```bash
cd services/data-store && docker compose up -d postgres  # then run -m integration from the root
```

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
  `[tool.uv.workspace] members = ["services/*"]`; both services are listed as workspace dependencies
  of the root package. This is why there's a single `.venv` and `uv.lock` instead of one per service.
- **Hook chain order is deliberate** (`.pre-commit-config.yaml`): autoflake → black → pycln → isort
  run first because they mutate files; flake8 runs last so it lints the final, formatted result.
  Preserve this order when editing the config.
- **flake8 config lives only in `.flake8`**, not duplicated as hook `args` — a prior project had
  `.flake8` and a `pyproject.toml` `[tool.flake8]` section drift apart, so this repo keeps a single
  source of truth.
- **Two-tier test gate**: `pre-commit` stage runs `pytest --testmon -x` (only tests touched by the
  change, so commits stay fast); `pre-push` stage runs the full suite. CI (`.github/workflows/ci.yml`)
  then runs the exact same `.pre-commit-config.yaml` via `pre-commit/action`, followed by the full
  suite again — so local hooks and CI can never disagree about what "passing" means.
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
