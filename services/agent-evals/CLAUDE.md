# CLAUDE.md (agent-evals)

The `agent_evals` package is the offline eval harness for the agents in `agent`, Pedro first. It
answers questions unit tests can't: is this prompt change better or worse, is this local model good
enough, did a model upgrade regress grounding. It is design only today: the package is an empty
skeleton. Read [`docs/architecture.md`](docs/architecture.md) before changing anything
structural, and update it in the same change (root `.claude/rules/architecture.md`).

## What it is, and what it isn't

- A library, not a service: no API, no database, no container, no schema.
- Built on pydantic-evals, which provides cases, datasets, repeats, judges and reports. This
  package owns only what's specific to Stakehold: the synthetic worlds and the fake
  `PortfolioClient`, the graders, the model × prompt matrix, the cost guard, baselines and the CLI.
- Kept out of service images. Once it has real dependencies it moves from a root dependency to the
  root `evals` extra (see "Packaging" in the architecture doc).

## Dependency direction

`agent_evals` imports `agent`: its `AgentRunner`, event types, `ChatModel` port and catalogue,
`PortfolioClient` Protocol and persona specs. `agent` never imports `agent_evals`, and a test in
`agent` enforces it. If the harness needs something `agent` doesn't expose, make it public API in
`agent`; don't reach into its internals.

## How it runs

The task function drives `agent`'s own `AgentRunner` in process, with the fake `PortfolioClient`
serving a synthetic world and a real `ChatModel` from the catalogue. No HTTP, no running services.
A case reports a pass rate over N repeats, never a single pass or fail. An errored case (provider
outage, harness bug) is reported apart from a failed one.

## Evals are not tests

- **Tests (free, deterministic):** the runner logic and adapter conformance live in `agent`'s unit
  tests (T0/T1). The one eval-side test is the harness smoke test, which runs a suite with the
  scripted fake model and checks the wiring at no cost. It runs with the normal unit suite.
- **Evals (paid, statistical, T2):** real models on synthetic cases. Started by hand only, never in
  pre-commit, pre-push or on a schedule; in CI only on manual dispatch or a PR label. See
  `.claude/rules/cost.md`.

Anything that can be checked deterministically belongs in a test or a code grader, not a judge.

```bash
uv run pytest services/agent-evals                 # free: unit tests and the fake-model smoke test
```

The paid CLI (`agent-evals run --suite smoke --model <catalogue id>`, `agent-evals compare`) isn't
built yet. When it is, don't run it yourself: say which suite and model, and the estimated cost.

## Results

Reports (JSON plus a markdown summary) go to a gitignored directory. When
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, runs also send their spans, with their synthetic content, to
`agent`'s self-hosted Phoenix under the `agent-evals` project, for reading transcripts. That's
optional: the smoke test and CI never need Phoenix, and Phoenix is never the record of a result. Baselines are committed, one
per suite. A change that intends to move a number updates the baseline in the same change.

## Rules

Scoped rules live in `.claude/rules/`: cost, and synthetic data only. They apply on top of the root
rules. Tests follow the root `CLAUDE.md`: no `__init__.py` under `tests/`.
