# agent-evals architecture

**Status: design only, first iteration. Nothing is built.** The package is an empty skeleton
(`src/agent_evals/__init__.py`) so the uv workspace resolves. The agent it evaluates is designed
in [`../../agent/docs/architecture.md`](../../agent/docs/architecture.md). Read that first,
because this harness drives that service's runner directly.

## What this is

An **offline eval harness** for the agents in `agent`, Pedro first. It holds:

- datasets of cases;
- graders that score an agent's behaviour on each case;
- a runner that executes cases across models and prompt versions;
- reports that compare a run with a committed baseline.

It answers questions unit tests can't:

- "Is this prompt change better or worse?"
- "Is this local model good enough to offer users who chose `local_only`?"
- "Did upgrading the model regress grounding?"

Evals exist from day one so that the first prompt and the first tool already have a number
attached. Every later change is then a measured delta, not an impression.

## Why a package, not a service, and not inside `agent`

- **Not a deployed service (yet).** An offline harness has no runtime surface: no API, no
  database, nothing to keep running. A service would mean a container, a schema and a deployment
  to maintain before there's a single production conversation to score. **Trigger to revisit:**
  online scoring of live conversations (tier 3 below), or wanting eval history over time as a
  dashboard rather than as files. At that point a service with its own storage earns its keep.
- **Not a directory inside `agent`.** A separate workspace package makes the dependency
  direction a fact, not a convention:
  - `agent_evals` imports `agent` (its runner, types, fakes);
  - `agent` never imports `agent_evals`. `agent`'s `pyproject.toml` doesn't depend on it, so the
    dependency graph says so. Because every image `uv sync`s the whole workspace, the package
    would still be importable at runtime. That's why `agent` gets a unit test asserting no module
    under `src/agent` imports `agent_evals`, instead of relying on review.

  It also keeps eval-only code and dependencies (judge prompts, report generation, dataset
  loaders) out of the runtime package's surface.

Consequence: `agent` must expose, as public API, what the harness needs. That's `AgentRunner`,
the event types, the `ChatModel` port and catalogue, the `PortfolioClient` Protocol, and the
persona specs. That pressure is healthy: anything the harness needs is something the service's
own tests need too.

## The tiers

| Tier | What | Where | Runs | Deterministic |
|---|---|---|---|---|
| T0 | Runner logic with a scripted fake `ChatModel` | `agent` unit tests | every commit | yes |
| T1 | Adapter conformance on recorded provider responses | `agent` unit tests | every commit | yes |
| T2 | **Offline evals**: real models on synthetic cases | **this package** | on demand, CI dispatch, nightly | no, so pass rates |
| T3 | Online evals: consented production traces | later, possibly a service | continuous | no |

T0 and T1 are *tests*. They are listed here because the boundary matters: anything that can be
checked deterministically belongs in T0/T1, where it's free and runs on every commit. T2 is for
what only a real model can show.

## Case model

A **case** is a self-contained scenario:

- **World:** a frozen, **synthetic** portfolio, i.e. the JSON responses `portfolio`'s API would
  return for one user (positions, holdings, returns, risk, a Labs backtest). The harness serves
  it through a fake `PortfolioClient`. No running services, no database, **never real user data
  and never the dev database**. The dev stack holds real portfolios, and copying them into a
  dataset would also send them to every model under test. Worlds are reusable across cases
  (e.g. "concentrated tech portfolio", "single bond ETF", "empty account", "unmapped
  instrument").
- **Page context:** what `web` would send (route, parameters).
- **Script:** one user message or several turns.
- **Consent policy** the run executes under, so refusals are testable.
- **Expectations:** which graders apply, with their parameters and thresholds.

Datasets are versioned files in this package (YAML or JSON; the choice is made at build time),
grouped into **suites**:

- `smoke`: a handful of cases, quick and cheap, the PR gate;
- `core`: the full regression set;
- one suite per concern (`grounding`, `refusals`, `voice`, `proposals`, …).

A dataset version is recorded on every result.

## Graders

**Deterministic code graders first, because they're cheap, exact and never disagree with
themselves.**

- **Tool use:** required tools were called, forbidden tools weren't, and the step count stayed
  within bounds. This is read from the runner's event stream, not from the text.
- **Numeric grounding.** Every figure in the answer (a percentage, an amount, a ratio) must
  trace to a value in the run's tool results, or to simple arithmetic over them, within a
  stated tolerance. This is the most important grader for a finance agent. A fluent answer with
  one invented volatility figure is worse than no answer, and only a mechanical check catches it
  reliably.
- **Forbidden claims:** guaranteed returns, instructions to trade, advice framed as certainty.
  Pattern- and list-based, kept deliberately narrow, and backed by the rubric grader for nuance.
- **Proposal validity (stage 2):** every `action_proposed` event is schema-valid, references
  instruments that exist in the world, and matches what the user asked for.
- **Correct refusal:** under `off` or `local_only` with no local model, the run ends with the
  typed consent event. On an empty portfolio, Pedro says there's nothing to evaluate rather than
  inventing holdings.
- **Failure honesty:** when a case's world makes a tool fail, the answer says so instead of
  filling the gap.

**Then LLM-judge rubric graders**, for what code can't judge:

- whether the evaluation is correct and complete given the world;
- whether suggestions are specific and actionable;
- whether the voice follows brand charter chapter IV.

Rules that make a judge trustworthy:

- a **pinned judge model** (a catalogue id, recorded on the result);
- a **versioned judge prompt** (content hash recorded);
- scores on a fixed rubric with written anchors, not a free-form "rate 1–10";
- **calibrated against a small human-labelled set**: the judge must agree with the labels above a
  threshold before its scores gate anything, re-checked whenever the judge model or prompt
  changes;
- the judge never sees which model produced the answer.

## Runner

- **Drives `agent`'s own `AgentRunner` in process**, with the fake `PortfolioClient` and a real
  `ChatModel` from the catalogue. It doesn't go over HTTP, so evals measure the agent loop, the
  prompts and the model, not the network. HTTP and SSE are covered by `agent`'s own tests.
- **Matrix:** cases × catalogue models × prompt versions × N repeats. Model outputs aren't
  deterministic, so a case reports a **pass rate over N**, not a single pass or fail. Thresholds
  are on rates, e.g. "grounding ≥ 0.95 across the suite".
- **Concurrency and cost guard.** Cases run concurrently, with a per-provider concurrency cap
  and a hard cost ceiling per invocation. The ceiling is estimated from the catalogue's pricing
  before starting, and the runner refuses to start above it without an explicit flag.
- **Local models** run through the same matrix. That grid, comparing a local model with the cloud
  reference on the same suite, is how a local model is judged good enough to offer `local_only`
  users. It's also how the Ollama-vs-LM Studio question gets settled.
- **Observability**, per [`.claude/rules/logging.md`](../../../.claude/rules/logging.md):
  - start: suite, case count, models, repeats, estimated cost;
  - one line per finished case;
  - a summary: passed, failed, errored, cost, wall time.

  An errored case (a provider outage, a harness bug) is reported separately from a failed case
  (the agent did badly), and never counted as either pass or fail.

## Results and baselines

- Each invocation writes a **JSON report** with:
  - git SHA, dataset version, prompt hashes, judge model and prompt hash;
  - catalogue ids, per-case results and grader details;
  - usage and cost.

  It also writes a **markdown summary** for humans. Reports go to a gitignored directory.
- **A committed baseline per suite** (the expected rates for the current default model and
  prompt) turns a run into a comparison: improved, regressed, or unchanged within noise. When a
  change *intends* to move a number, updating the baseline is part of that change and is reviewed
  like code.

## When it runs

- **Locally:** a CLI, e.g. `agent-evals run --suite smoke --model anthropic:<model>`, plus
  `agent-evals compare <report> <baseline>`.
- **CI:** the `smoke` suite on **manual dispatch or a PR label**, never automatically on every
  push. It costs money, needs provider keys as CI secrets, and its non-determinism would make it
  a flaky gate. The full `core` suite runs nightly or before a release.
- **Never in pre-commit.** It isn't part of `CLAUDE.md`'s "two-tier test gate", which stays
  deterministic.

## Tier 3, later: online evals

Once Pedro has real users, the run traces `agent` already records (see its "Observability"
section) become the input:

- sample runs and score them with the same code graders (grounding works on any trace with its
  tool results);
- let users flag bad answers;
- promote interesting traces to T2 cases.

**Promotion requires the user's consent and anonymization**: the real portfolio is replaced by a
synthetic world with the same shape. That's a policy to define then. This tier is what would
justify a dedicated eval service.

## Open questions

- **Dataset format** (YAML vs JSON) and the fixture shape of a world. Decide at build time; the
  world should reuse `portfolio`'s own response shapes so the fake can't drift from the real API.
- **Grounding tolerance and its arithmetic scope.** Which derived figures count as "traceable"
  (sums and shares, yes; anything model-estimated, no). Fix it with the first real cases.
- **Who labels the judge-calibration set**, and how large it needs to be before rubric scores
  can gate a change.
