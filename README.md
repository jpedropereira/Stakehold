# Stakehold

Stakehold is an application to manage different financial portfolios, calculate
their returns, and identify possible improvements.

It's a uv workspace containing two services:

- `services/data-store/` — the `data_store` package
- `services/portfolio/` — the `portfolio` package

## Development setup

This project uses Python 3.14 and [uv](https://docs.astral.sh/uv/) for dependency
management, with a shared virtual environment and lockfile across both services.

```bash
uv sync --all-extras --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Run the test suite for both services:

```bash
uv run pytest services
```

Commits run a fast, testmon-scoped subset of tests plus formatting/lint/secret
checks; pushes run the full test suite. See `.pre-commit-config.yaml` for the
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
`pre-commit/action`, then the full test suite — so CI can't drift from what
runs locally.
