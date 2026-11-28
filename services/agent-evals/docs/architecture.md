# agent-evals architecture

**Status: design only, first iteration. Nothing is built.** The package is an empty skeleton
(`src/agent_evals/__init__.py`) so the uv workspace resolves. The harness it will be built on,
**pydantic-evals**, was chosen by a spike against Inspect AI on 2026-11-22 (see "Harness
choice"). The agent it evaluates is designed in
[`../../agent/docs/architecture.md`](../../agent/docs/architecture.md). Read that first, because
this package drives that service's runner directly.

## What this is

The **offline evals** for the agents in `agent`, Pedro first. They're built on an existing
harness rather than from scratch. The generic machinery comes from pydantic-evals:

- cases and datasets;
- repeats and concurrency;
- judge plumbing;
- reports and baseline diffs.

This package owns only what's specific to Stakehold. That split is in "What the harness provides,
and what we build".

It answers questions unit tests can't:

- "Is this prompt change better or worse?"
- "Is this local model good enough to offer users who chose `local_only`?"
- "Did upgrading the model regress grounding?"

Evals exist from day one so that the first prompt and the first tool already have a number
attached. Every later change is then a measured delta, not an impression.

## Cost: kept low on purpose

Stakehold is a hobby project, and every real-model eval is paid for out of pocket. So evals are
designed to cost as little as possible, and this constraint wins when it conflicts with
coverage. In practice:

- **No paid eval runs unattended.** Real-model evals run only when someone starts them, and
  only when a change could move Pedro's behaviour: a prompt, a model, a tool, the agent loop.
  There is no nightly or scheduled run, because it would spend money on days nothing changed.
- **Free checks do as much of the work as possible.** Anything that can be checked
  deterministically is a T0/T1 test or a code grader (grounding, required tools), which costs
  nothing. The LLM judge is used only for what code can't judge, since each judge call is one
  more paid call per case and repeat.
- **Small by default:** the `smoke` suite stays a handful of cases with few repeats. The spike's
  3 repeats were enough for the made-up-figure case to fail every time.
- **One cloud model by default.** The matrix covers several cloud models only when one is being
  chosen. Local models cost nothing per call, so they can be added freely.
- **A cheap judge,** pinned like any other model. A local judge, which costs nothing per call, is
  an option once it has been calibrated (see "Open questions").
- **A low default cost ceiling** (see "Runner"), so a bigger run needs a deliberate flag.

## Why a package, not a service, and not inside `agent`

- **Not a deployed service (yet).** An offline harness has no runtime surface: no API, no
  database, nothing to keep running. A service would mean a container, a schema and a deployment
  to maintain before there's a single production conversation to score. **Trigger to revisit:**
  online scoring of live conversations (tier 3 below), or wanting eval history over time as a
  dashboard rather than as files. At that point a service earns its keep. The self-hosted
  tracing tool is already decided: `agent` runs a Phoenix for its traces, and eval runs send their
  spans there too (see "Viewing runs in Phoenix").
- **Not a directory inside `agent`.** A separate workspace package makes the dependency
  direction part of the dependency graph:
  - `agent_evals` imports `agent` (its runner, types, fakes);
  - `agent` never imports `agent_evals`, and `agent`'s `pyproject.toml` doesn't depend on it.

  In the developer and CI environment both packages share one venv, so a stray import would still
  resolve there. That's why `agent` gets a unit test asserting that no module under `src/agent`
  imports `agent_evals`, or `pydantic_ai`, which arrives with the harness (see "Harness choice").
  The rule is checked by a test, not left to review.

Consequence: `agent` must expose, as public API, what the harness needs:

- `AgentRunner` and the event types;
- the `ChatModel` port and the catalogue;
- the `PortfolioClient` Protocol;
- the persona specs.

That pressure is healthy: anything the harness needs is something the service's own tests need
too.

## Harness choice: pydantic-evals

### The constraint that decides it

The agent design's central testing claim is that **evals measure what ships**:

- our `AgentRunner`;
- our prompts and tools;
- **our provider adapters calling the real provider SDKs**, against a synthetic world served by a
  fake `PortfolioClient`.

A harness that runs its own agent loop, or that intercepts the model call and makes it on our
behalf, measures something slightly different from production. A real model call should leave
our process through our adapter, as it would in production.

### The spike

Both candidates were tried against the same system under test:

- a miniature `ChatModel` port;
- the runner loop with a step budget;
- one read tool, with identity from context;
- a fake portfolio client serving two synthetic worlds;
- a scripted fake model;
- grounding and required-tool graders written once as plain functions.

There were three cases. One of them injects a made-up volatility figure, so that a grader passing
everything couldn't pass for evidence.

All of it ran on Python 3.14.7, in isolated throwaway projects with no API calls. The spike code
wasn't kept.

| Criterion | pydantic-evals 2.71.0 | Inspect AI 0.3.291 |
|---|---|---|
| Runs our runner unchanged, with our adapters calling providers | **Yes, natively.** The task is any async callable `inputs -> output`. | Two ways. **(a) A custom solver** runs the runner unchanged, but Inspect records no model usage or model transcript, and a placeholder `--model` is still required. **(b) `agent_bridge()`** also runs our runner and adapter, with the model name set to `"inspect"`. Inspect then **redirects the SDK call to its own provider**, which records usage and a transcript, but the call that reaches the provider is Inspect's, not ours. |
| Install on 3.14, added footprint | Works. 23 packages, 36 MB. | Works. 79 packages, 225 MB (boto3, aiobotocore, s3fs, numpy, textual, fastapi, uvicorn, debugpy, …), plus the `openai` SDK for the bridge. |
| Glue for 3 cases and 3 graders | about 100 lines, including a fake judge and report persistence | about 85 lines (solver), about 95 lines (bridge, including a minimal adapter) |
| Graders read structured run data | Yes. The evaluator reads the task's return value (`ctx.output`): answer, events, tool results. | Yes. The solver writes the run to the per-sample `store()`, and scorers read it. |
| The made-up figure fails grounding | Yes, 3 of 3 repeats | Yes, in both variants |
| Repeats | `repeat=N`, with runs grouped per case. We compute pass rates (a few lines). | `Epochs(N, reducers)`, with built-in `mean`, `pass_at_k`, `at_least_k` |
| Baseline comparison | **Built in**: `report.print(baseline=…)` diffs assertions, scores and durations per case | None. We would compare `.eval` logs ourselves. |
| Persisted results | Datasets to and from YAML/JSON with a generated JSON Schema (`Dataset.to_file`). **There's no report save/load; we serialize the report ourselves** (`dataclasses.asdict` worked). | `.eval` logs, `read_eval_log` |
| Viewer | None locally. The span and trace view is Logfire, a hosted service. | **`inspect view`**: a local web viewer (transcripts, scores, metadata) |
| Usage and cost for our direct calls | Whatever our runner reports | Only for bridged calls. Per-sample `token_limit`/`cost_limit` likewise apply only there. |
| LLM judge | `LLMJudge(rubric, model=…)`, judge pinned per evaluator; runs through pydantic-ai models. The mechanics were verified with pydantic-ai's `FunctionModel`. | `model_graded_qa`/`model_graded_fact`, with a separately pinned grader model or role |
| Stability | "Production/Stable" classifier. Released in lockstep with pydantic-ai, with an **exact pin on `pydantic-ai-slim`**. | "Beta" classifier, frequent releases (ten in the month before the spike) |

### Decision and reasoning

**pydantic-evals.** Confidence: medium-high.

- **It fits the constraint without bending it.** The eval task *is* our runner, our adapters
  make the real calls, and nothing is intercepted. Inspect's richest mode, the bridge, gets its
  usage, transcripts and limits by taking over the provider call. That means it doesn't
  evaluate our adapter's real request: generation parameters are dropped unless
  `forward_generation_config=True`, and it's unverified whether provider-specific hints such as
  Anthropic cache breakpoints, and streaming, survive the translation. Inspect's other mode, the
  custom solver, keeps our call path but loses most of what Inspect would have added.
- **Its built-in feature is the one our main workflow needs.** That workflow is "change a prompt
  or model, compare with the baseline", which is exactly `report.print(baseline=…)`. Inspect's
  stronger features (the viewer, pass@k reducers, `eval_set` retries) are good, but not this
  workflow's critical path.
- **It's small enough to live in a shared lockfile.** The workspace has one `uv.lock`, so every
  harness dependency constrains what the runtime services can resolve. Inspect adds 79 packages,
  including its own lower bounds on `fastapi`, `pydantic` and `anyio`, which `web`, `auth` and
  `portfolio` depend on, plus an upper bound on `fsspec`. pydantic-evals adds 23. Images avoid
  the harness either way (see "Packaging"), but the lockfile coupling stays.

What we give up, said plainly, and what we build to make up for it:

- **No local viewer.** This was the weakest point of the choice. It's now covered by `agent`'s
  self-hosted Phoenix: eval runs send their spans there, with their synthetic content, so a
  failed case can be read as a transcript (see "Viewing runs in Phoenix"). Without Phoenix
  configured, failed cases are read through the report's `include_reasons` output and our own JSON
  reports, as before.
- **No report persistence.** We serialize it ourselves.
- **No pass@k reducers.** We compute pass rates.
- **No token or cost limits.** We keep our own run-wide cost ceiling, which neither harness
  enforces for direct calls anyway.

**`pydantic-ai-slim` enters the workspace through the harness.** pydantic-evals' docs say it
"does not depend on pydantic-ai", but its package metadata pins `pydantic-ai-slim` exactly, and
the spike's install confirmed it. That's acceptable because the judge isn't the system under test
and is free to use pydantic-ai's model layer. But it puts a framework the agent design rejected
for its own loop (`agent/docs/architecture.md`, "Provider abstraction") into the same venv as
`agent`. The import test above keeps it out of `src/agent`.

**Triggers to revisit:**

- we need per-sample token or cost limits enforced by the harness;
- comparing many runs over time outgrows two-report diffs, and Phoenix's span views aren't enough.

Transcript browsing and a log store for tier 3 were triggers too. Phoenix now covers both. The
likely move, if a trigger fires, is Inspect with the custom-solver variant. The domain graders are
plain functions with thin harness wrappers, so a switch rewrites wrappers, not graders.

### Alternatives considered after the spike (2026-11-24)

When observability was planned (PD-5), two more tools were weighed against this choice. The full
findings are in `agent`'s [`observability.md`](../../agent/docs/observability.md), under
"Rejected alternatives" and "Sources".

- **DeepEval: rejected.** Its many agent metrics are almost all LLM-judged, so each is a paid call.
  It also doesn't fit the workspace:
  - it loads `.env` files at import, against the root conftest's guard and
    [`synthetic-data-only.md`](../.claude/rules/synthetic-data-only.md);
  - its `pytest11` plugin loads in every pytest run in the workspace;
  - it adds about 80 packages to the shared lockfile;
  - its telemetry is on by default;
  - marking a run as the regression baseline needs its hosted service.
- **Phoenix experiments as the harness: not adopted.** `run_experiment` can run our runner
  unchanged with plain-function evaluators. But it needs the Phoenix server for every run,
  including the free smoke test and CI. Baselines would live in Phoenix's database rather than be
  reviewed in the repo, and `arize-phoenix-evals` is under the Elastic License. Phoenix is used for
  viewing runs instead, which doesn't change the harness.

## What the harness provides, and what we build

| From pydantic-evals | Ours (this package, plus fakes exported by `agent`) |
|---|---|
| `Case` / `Dataset`, YAML/JSON files with a generated schema | **Worlds**: synthetic `portfolio` API responses, plus the fake `PortfolioClient` that serves them |
| `Evaluator` base class and `EvaluatorContext` | **Domain graders** as plain functions, each with a thin `Evaluator` wrapper (see "Graders") |
| `LLMJudge`: rubric, pinned judge model | **Rubrics**, the human-labelled calibration set, and the calibration check |
| `Dataset.evaluate(task, repeat=…, max_concurrency=…, retry_task=…, task_timeout=…)` | **The task function** that builds the persona, model, tools and context and calls `AgentRunner` |
| `EvaluationReport`, `print(baseline=…)`, `case_groups()` | Pass-rate aggregation over repeats; report persistence with run metadata; thresholds |
| (nothing) | The **model × prompt-version matrix** loop, the **run-wide cost ceiling**, and the CLI |

## Packaging: keeping the harness out of service images

Every service Dockerfile runs `uv sync --frozen` over the whole workspace, which installs the root
project's dependencies plus the default `dev` group. Today `agent-evals` is a **root
dependency**, so once it gains real dependencies they would ship in every image, `auth`'s and
`web`'s included.

**Mechanism, verified in the spike** on a scratch copy of the workspace's manifests and lock:

1. Drop `agent-evals` from the root `dependencies`.
2. Add it as a root optional extra, `[project.optional-dependencies] evals = ["agent-evals"]`.

Results with `uv sync --frozen --dry-run`:

| Command | Who runs it | pydantic-evals installed? |
|---|---|---|
| `uv sync --frozen` | the images | no |
| `uv sync --frozen --no-dev` | — | no |
| `uv sync --frozen --all-extras --dev` | `CLAUDE.md`'s setup and CI | yes |

Neither the setup command nor CI changes. The Dockerfiles keep `COPY`ing `services/agent-evals`,
because it's still a workspace member.

This change lands with the first real eval code, not before. The skeleton has no dependencies yet,
so today's root dependency costs nothing.

## The tiers

| Tier | What | Where | Runs | Deterministic |
|---|---|---|---|---|
| T0 | Runner logic with a scripted fake `ChatModel` | `agent` unit tests | every commit | yes |
| T1 | Adapter conformance on recorded provider responses | `agent` unit tests | every commit | yes |
| T2 | **Offline evals**: real models on synthetic cases | **this package** (pydantic-evals) | on demand only: locally, CI dispatch or PR label | no, so pass rates |
| T3 | Online evals: consented production traces | later, possibly a service | continuous | no |

T0 and T1 are *tests*. They are listed here because the boundary matters: anything that can be
checked deterministically belongs in T0/T1, where it's free and runs on every commit. T2 is for
what only a real model can show. T1 is also what covers adapter correctness against each
provider's wire format, so T2 can concentrate on model, prompt and loop behaviour.

The harness itself also gets a deterministic smoke test. A T2 suite run with the scripted fake
model, as in the spike, checks that the task wiring, evaluators and reports work, at no cost. It
runs in the normal unit run.

## Case model

A pydantic-evals `Case` per scenario. Its `inputs` hold:

- **World id:** names a frozen, **synthetic** portfolio, i.e. the JSON responses `portfolio`'s
  API would return for one user (positions, holdings, returns, risk, a Labs backtest). The task
  function serves it through the fake `PortfolioClient`. No running services, no database,
  **never real user data and never the dev database**. The dev stack holds real portfolios, and
  copying them into a dataset would also send them to every model under test. Worlds are reusable
  across cases (e.g. "concentrated tech portfolio", "single bond ETF", "empty account", "unmapped
  instrument").
- **Page context:** what `web` would send (route, parameters).
- **Script:** one user message or several turns.
- **Consent policy** the run executes under, so refusals are testable.

Case-specific expectations go in the case's `expected_output` and `metadata`, e.g. required
tools or a tolerance. Evaluators apply at dataset level, or per case where only some cases need
them.

**Datasets are YAML files** in this package, written and read with `Dataset.to_file` /
`from_file`. They're validated against the JSON Schema pydantic-evals generates, which needs our
custom evaluator types passed in. They're grouped into **suites**:

- `smoke`: a handful of cases, quick and cheap, run on a PR when it's labelled;
- `core`: the full regression set, run by hand, e.g. before changing the default model;
- one suite per concern (`grounding`, `refusals`, `voice`, `proposals`, …).

A dataset version is recorded on every result. Datasets are declared with explicit generic
parameters (`Dataset[Inputs, Output, Metadata]`). The spike's untyped dataset serialized with a
warning and fell back to `Any`.

## Graders

Each grader is a **plain function over the run's structured result** (answer, events, tool
results), wrapped in a thin `Evaluator` subclass that returns `EvaluationReason(value, reason)`.
The functions don't know which harness calls them. That's what makes the harness replaceable, and
lets the same checks run against tier-3 traces later.

**Deterministic code graders first, because they're cheap, exact and never disagree with
themselves.**

- **Tool use:** required tools were called, forbidden tools weren't, and the step count stayed
  within bounds. This is read from the runner's events, not from the text.
- **Numeric grounding.** Every figure in the answer (a percentage, an amount, a ratio) must
  trace to a value in the run's tool results, or to simple arithmetic over them, within a stated
  tolerance. This is the most important grader for a finance agent. A fluent answer with one
  invented volatility figure is worse than no answer, and only a mechanical check catches it. The
  spike's minimal version already caught the injected figure on every repeat in both harnesses.
- **Forbidden claims:** guaranteed returns, instructions to trade, advice framed as certainty.
  Pattern- and list-based, kept deliberately narrow, and backed by the rubric grader for nuance.
- **Proposal validity (stage 2):** every `action_proposed` event is schema-valid, references
  instruments that exist in the world, and matches what the user asked for.
- **Correct refusal:** under `off` or `local_only` with no local model, the run ends with the
  typed consent event. On an empty portfolio, Pedro says there's nothing to evaluate rather than
  inventing holdings.
- **Failure honesty:** when a case's world makes a tool fail, the answer says so instead of
  filling the gap.

**Then `LLMJudge` rubric graders**, for what code can't judge:

- whether the evaluation is correct and complete given the world;
- whether suggestions are specific and actionable;
- whether the voice follows brand charter chapter IV.

Rules that make a judge trustworthy:

- a **pinned judge model** per `LLMJudge` instance, recorded on the result. The judge goes
  through pydantic-ai's model layer, not our `ChatModel` port. That's acceptable because the judge
  isn't the system under test, but it means judge models are configured as pydantic-ai model
  strings, separately from our catalogue.
- a **versioned rubric** (content hash recorded), stored as `rubrics/<name>/v<N>.md` in this
  package. A rubric is edited in place until a committed baseline records its hash, and is never
  edited after that. Reports in the gitignored results directory don't freeze it. The first is `rubrics/voice/v1.md`. Its
  reasoning is in `agent`'s `docs/persona.md`;
- scores on a fixed rubric with written anchors, not a free-form "rate 1–10". Each dimension is
  **pass/fail**, with a pass anchor, a fail anchor and labelled borderline examples, and is judged
  in its own call. Binary labels make agreement with a human labeller simple to measure, and a
  per-dimension verdict says *which* principle failed. A 0–2 scale and a single holistic score were
  considered: the first is harder to label consistently, and the second can't localise a failure;
- **calibrated against a small human-labelled set**: the judge must agree with the labels above a
  threshold before its scores gate anything, re-checked whenever the judge model or rubric
  changes. pydantic-evals' report evaluators (e.g. a confusion matrix) are a candidate for the
  agreement measure; not yet tried.
- the judge never sees which model produced the answer.

## Runner

- **The task function drives `agent`'s own `AgentRunner` in process**, with the fake
  `PortfolioClient` and a real `ChatModel` from the catalogue. It doesn't go over HTTP, so evals
  measure the agent loop, the prompts and the model, not the network. HTTP and SSE are covered by
  `agent`'s own tests. It returns the run's structured result: answer, events, tool results,
  usage, cost. Every evaluator reads that.
- **Repeats:** `repeat=N`. Model outputs aren't deterministic, so a case reports a **pass rate
  over N**, computed from `report.case_groups()`, not a single pass or fail. Thresholds are on
  rates, e.g. "grounding ≥ 0.95 across the suite".
- **Matrix:** our loop over catalogue models × prompt versions, one `Dataset.evaluate` call per
  cell, each labelled with `name`, `task_name` and `metadata`.
- **Concurrency, retries, timeouts:** `max_concurrency` per evaluation, with a per-provider cap
  enforced by our matrix loop. `retry_task` for transient provider errors that our adapter's own
  retries didn't absorb. `task_timeout` per case.
- **Cost guard (ours).** A hard cost ceiling per invocation, estimated from the catalogue's
  pricing before starting, and checked against actual usage as cells finish. The runner refuses
  to start above the ceiling without an explicit flag. The default ceiling is low, sized for a
  `smoke` run on one model (see "Cost: kept low on purpose").
- **Local models** run through the same matrix. That grid, comparing a local model with the cloud
  reference on the same suite, is how a local model is judged good enough to offer `local_only`
  users. It's also how the Ollama-vs-LM Studio question gets settled.
- **Observability**, per [`.claude/rules/logging.md`](../../../.claude/rules/logging.md):
  - start: suite, case count, models, repeats, estimated cost;
  - one line per finished matrix cell;
  - a summary: passed, failed, errored, cost, wall time.

  An errored case (a provider outage, a harness bug) is reported separately from a failed case
  (the agent did badly), and never counted as either pass or fail.

## Viewing runs in Phoenix

`agent`'s runner emits OpenTelemetry spans whichever driver runs it (see `agent`'s
[`observability.md`](../../agent/docs/observability.md)). The harness's CLI installs the SDK the
same way the API does, with two differences:

- **Content is recorded.** The harness sets `record_content`, which production never does, so a
  case's spans hold the user message, the model's answer and the tool results. The worlds are
  synthetic by rule, so that's safe, and it's what makes a failed case readable.
- **The spans go to the `agent-evals` project**, set through
  `OTEL_RESOURCE_ATTRIBUTES=openinference.project.name=agent-evals`, apart from production runs.

**Export is optional.** The harness exports only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. The
free fake-model smoke test, the unit run and CI set nothing and need no Phoenix. A paid run on a
developer's machine sets the endpoint and the Phoenix API key to browse its transcripts. Each JSON
report records the trace ids of its cases, so a failed case in the report can be found in Phoenix.

Phoenix is a viewer here, not the record: pass rates, baselines and costs still come from our
reports and the catalogue's pricing.

## Results and baselines

- **Persisted by us.** pydantic-evals has no report save/load. Each invocation writes a **JSON
  report** (the serialized `EvaluationReport`) with:
  - git SHA, dataset version, prompt hashes, judge model and rubric hash;
  - catalogue ids, per-case results and grader reasons;
  - usage and cost.

  It also writes a **markdown summary** for humans. Reports go to a gitignored directory.
- **Baselines.** A committed baseline per suite records the expected pass rates for the current
  default model and prompt. A run compares its rates with the baseline: improved, regressed, or
  unchanged within noise. When comparing two runs side by side, `report.print(baseline=…)` gives
  the per-case diff. When a change *intends* to move a number, updating the baseline is part of
  that change and is reviewed like code.

## When it runs

- **Locally:** a thin CLI over the above, e.g. `agent-evals run --suite smoke --model
  anthropic:<model>`, plus `agent-evals compare <report> <baseline>`.
- **CI:** the `smoke` suite on **manual dispatch or a PR label**, never automatically on every
  push. It costs money, needs provider keys as CI secrets, and its non-determinism would make it
  a flaky gate. The full `core` suite is run by hand, never on a schedule (see "Cost: kept low
  on purpose"). The fake-model harness
  smoke test (see "The tiers") is the exception: it's free and deterministic, and runs with the
  unit suite.
- **Never in pre-commit**, apart from that free smoke test. Real-model evals aren't part of
  `CLAUDE.md`'s "two-tier test gate", which stays deterministic.

## Tier 3, later: online evals

Once Pedro has real users, the run traces `agent` already sends to Phoenix (see its
"Observability" section) become the input:

- sample runs and score them with the same plain-function graders (grounding works on any trace
  with its tool results);
- let users flag bad answers;
- promote interesting traces to T2 cases.

**Promotion requires the user's consent and anonymization**: the real portfolio is replaced by a
synthetic world with the same shape. That's a policy to define then.

Production spans carry no content, so a grader that needs the answer or the tool results reads
them from `agent`'s `messages`, and the run's structure from Phoenix.

pydantic-evals' span-based evaluators could score OpenTelemetry spans directly, but they need the
Logfire SDK configured. Whether a plain OpenTelemetry setup feeds them isn't documented, and
wasn't tested. This tier is also what would justify a dedicated eval service.

## Open questions

- **Version-pin cadence.** pydantic-evals and `pydantic-ai-slim` release in lockstep, with an
  exact pin between them. Decide how often to upgrade, and treat an upgrade as an eval-affecting
  change: re-run the smoke suite against the baseline.
- **The world's fixture shape.** It should reuse `portfolio`'s own response shapes so the fake
  can't drift from the real API.
- **Grounding tolerance and its arithmetic scope.** Which derived figures count as "traceable"
  (sums and shares, yes; anything model-estimated, no). Fix it with the first real cases.
- **Who labels the judge-calibration set**, and how large it needs to be before rubric scores
  can gate a change. Phoenix's annotation UI, on the `agent-evals` transcripts, is a candidate
  place to do the labelling.
