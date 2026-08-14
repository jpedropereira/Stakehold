# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Stakehold is an application to manage financial portfolios, calculate their returns, and identify
possible improvements. It's a uv workspace containing two services, both currently empty scaffolding
with no business logic yet:

- `services/data-store/` — the `data_store` package
- `services/portfolio/` — the `portfolio` package

They share one virtual environment and one lockfile (`uv.lock`) at the workspace root. Python 3.14 is
required.

## Commands

### Setup

```bash
uv sync --all-extras --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

### Tests

```bash
uv run pytest services                                                        # full suite, both services
uv run pytest services/portfolio                                              # one service
uv run pytest services/portfolio/tests/test_placeholder.py::test_placeholder  # single test
uv run pytest services --testmon --tb=short -x                                # fast, testmon-scoped (what pre-commit runs on commit)
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
security boundaries, testing philosophy, and context/session management live in
`.claude/rules/*.md` and apply automatically. The following aren't covered there:

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
