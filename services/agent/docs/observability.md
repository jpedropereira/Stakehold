# agent observability and consumption

**Status: agreed design for v1 is pending owner sign-off (PD-5). Nothing here is built.** This
document decides how every run is made visible and how its consumption is recorded and reported.
PD-6 turns section "Fields PD-6's schema must hold" into DDL. PD-11 (runner), PD-13 (Anthropic
adapter and catalogue), PD-21 (persistence) and PD-23 (traces and the usage report) build the
rest. When one of them changes a decision here, it changes this document in the same PR, per
[`.claude/rules/architecture.md`](../../../.claude/rules/architecture.md).

The short form of the money rules is in
[`../.claude/rules/consumption.md`](../.claude/rules/consumption.md). This document holds the
reasoning.

## Three outputs, three jobs

A run produces three kinds of record. They are kept apart on purpose, because they have different
readers, different durability and different rules about content.

| Output | Reader | Where it goes | Durability | May contain content? |
|---|---|---|---|---|
| **Container logs** | an operator, live | stderr, via `shared.logging_config` | as long as the container's log | No. Ids, names, counts, cost, timings, status only. |
| **Traces** (spans) | a developer debugging a run after the fact, in a UI | a self-hosted **Phoenix**, over OTLP from the OpenTelemetry SDK; Phoenix stores them in `app-postgres` under its own schema | best effort: a span can be dropped | Not from production: content lives in `messages`, and a span refers to it by id. Eval runs, whose data is synthetic, do send content. |
| **Consumption** (`model_calls`) | the owner, the usage report, the daily budget | `agent.model_calls` in `app-postgres`, written by `observability/`'s service | durable, transactional: never dropped | No. Counts, cost and status only. |

The split that matters most is between the last two. **Spans are diagnostics; `model_calls` is
the money record.** The SDK's batching processor drops spans when its queue is full, and drops a
batch whose export fails, for instance while Phoenix is down (see "Traces"). That is acceptable
for a debugging aid and unacceptable for spend. So consumption rows never come from spans or from
Phoenix. They are written by the service, in their own transactions, as each model call finishes.

## Log lines

Logging follows the root [`logging.md`](../../../.claude/rules/logging.md): a start line, a
checkpoint per unit of work, and a completion summary. Every entry point calls
`shared.logging_config.configure_logging()`, which installs the stderr handler. The format is that
helper's text format, unchanged, with **`key=value` pairs** in the message, so a line is readable
by eye and still easy to grep or parse. No JSON formatter: it would make `agent` the one service
whose logs look different, for an aggregator that doesn't exist yet.

The runner logs, under the logger `agent.runtime`. It doesn't know about HTTP or Postgres, but
logging to stderr isn't a transport, and logging in the runner means the eval harness gets the
same lines as the API for free.

```text
INFO    agent.runtime: run_started run=<uuid> conversation=<uuid> user=<uuid> persona=pedro model=anthropic:<model> route=/portfolio preload=1
INFO    agent.runtime: tool_call run=<uuid> step=0 tool=get_holdings preloaded=true status=ok ms=212 result_chars=3104
INFO    agent.runtime: model_call run=<uuid> step=1 attempt=1 model=anthropic:<model> status=ok in=412 out=388 cache_r=5120 cache_w=0 cost_usd=0.004210 ms=6230 stop=tool_use
INFO    agent.runtime: tool_call run=<uuid> step=1 tool=get_performance preloaded=false status=ok ms=180 result_chars=922
INFO    agent.runtime: model_call run=<uuid> step=2 attempt=1 model=anthropic:<model> status=ok in=1310 out=640 cache_r=5120 cache_w=0 cost_usd=0.007905 ms=9811 stop=end_turn
INFO    agent.runtime: run_finished run=<uuid> outcome=answered steps=2 calls=2 tools=2 in=1722 out=1028 cache_r=10240 cache_w=0 cost_usd=0.012115 ms=16490
```

| Line | When | Fields |
|---|---|---|
| `run_started` | before the first tool or model call | run, conversation, user (an opaque UUID), persona, catalogue model id, page route, number of preloaded tools |
| `tool_call` | each tool call finishes, preloaded ones included | step, tool name, preloaded, status (`ok`, `error`, `refused`), latency, result size in characters |
| `model_call` | each model call attempt finishes, retries included | step, attempt, catalogue model id, status (`ok`, `error`, `aborted`), the four token counts, cost, latency, stop reason |
| `run_finished` | the run ends, whatever the outcome | outcome, steps, model calls, tool calls, the four token totals, cost, wall time |

`outcome` is one of `answered`, `budget_exceeded:<steps|input_tokens|output_tokens|wall_clock|cost>`,
`consent_required`, `no_permitted_model`, `provider_unavailable`, `aborted` (the client went
away) or `error:<type>`. A run that stopped on a budget or a consent refusal says so in this
field as plainly as one that answered. That's the rule's "nothing to do vs something went wrong"
distinction applied to a turn.

**Levels**, the same convention as `data-store`'s logging rule:

- `info`: the lines above, when the status is `ok` and the outcome is `answered`.
- `warning`: something the run survived. A tool that failed or was refused, a model call attempt
  that failed and was retried, a tripped budget, a consent refusal.
- `error`: a run that failed (`provider_unavailable`, `error:<type>`). Use `logger.exception`
  where there's a traceback, but the traceback must not carry content: a provider error message
  can echo part of the request, so adapter exceptions are re-raised as our typed errors with the
  provider's error code and status only.

The persistence side (`api/`) adds one line of its own when it can't record a run or a model
call: `warning`, with the run id and the error class. That is the one failure that would
otherwise hide spend.

**Tests** assert these lines with `caplog`, the way the root rule asks progress output to be
tested: the start line, one line per call, and a summary whose outcome matches the run.

## Traces

### Phoenix stores and shows the spans

Spans go to a self-hosted [Phoenix](https://github.com/Arize-ai/phoenix) (Arize), over OTLP. We
don't keep our own span table. Phoenix gives us what a table would not:

- **A UI to read a run.** Each run is a tree of model and tool calls with timings, tokens and
  status. For eval runs, where content is allowed, you can read the whole transcript. The
  eval harness (pydantic-evals) has no local viewer, and the eval doc names that as the weakest
  point of choosing it. Phoenix fills that gap, for the eval runs and the production runs alike.
- **History over time**, per project: token, latency, error and cost views without writing SQL.
- **One place for both kinds of run.** That's the bridge to tier-3 online evals (see the eval doc).

Phoenix is *not* the money record (`model_calls` is) and not the eval harness (pydantic-evals is).
See "Rejected alternatives" for why its experiments and evals library aren't used as the harness.

### The OpenTelemetry SDK, wired per entry point

`agent` instruments with OpenTelemetry. The split between the OTel *API* and *SDK* is what keeps
the layering intact:

- **`runtime/` and `models/adapters/` import only the API** (`opentelemetry.trace`). Without an SDK
  installed, the API is a no-op ("in the absence of an installed SDK, the Trace API is a 'no-op'
  API", OTel trace API spec). So a unit test that builds a runner and nothing else records no
  spans and needs nothing.
- **`observability/` owns the SDK setup**: a function that builds a `TracerProvider` with the
  processors and exporters the driver asks for, and the attribute-name constants. There is no
  exporter of our own: Phoenix takes the standard OTLP exporter
  (`opentelemetry-exporter-otlp-proto-http`).
- **Each driver picks its exporters:**
  - `agent serve`: a `BatchSpanProcessor` around the OTLP HTTP exporter, sending to Phoenix's
    `/v1/traces`. Spans leave off the request path, so a slow or absent Phoenix never delays a
    stream.
  - the eval harness: the same OTLP exporter to the same Phoenix, under the project
    `agent-evals`, **only when an OTLP endpoint is configured**. Without one, evals export
    nothing. The free fake-model smoke test and CI need no Phoenix.
  - tests: a `SimpleSpanProcessor` around the SDK's in-memory exporter, so assertions read spans
    with nothing running.
- **Configuration is the standard OTel environment**, read by the SDK, not settings of ours:
  - `OTEL_EXPORTER_OTLP_ENDPOINT`: Phoenix's address on `stakehold-net`;
  - `OTEL_EXPORTER_OTLP_HEADERS`: `authorization=Bearer <Phoenix API key>`, a credential;
  - `OTEL_SERVICE_NAME=agent`;
  - `OTEL_RESOURCE_ATTRIBUTES=openinference.project.name=agent` (or `agent-evals`): the Phoenix
    project the spans land in. Verified in the spike;
  - `OTEL_BSP_*` to tune the batch processor;
  - `OTEL_SDK_DISABLED=true` to turn tracing off (spec-defined; confirm the Python SDK honours it
    when PD-23 wires it).

**What we accept by batching.** The batch processor's defaults are a 2048-span queue, a 5 s
schedule, 512 spans per export and a 30 s export timeout (`OTEL_BSP_*`). "After the size is
reached, spans are dropped", and a failed export drops its batch. At one run per user turn, with
a handful of spans each, the queue doesn't come close. A Phoenix outage loses the spans sent
while it was down and nothing else: runs, messages and `model_calls` don't touch Phoenix. The API's
lifespan shutdown calls the provider's `shutdown()`, which flushes, so a deploy restart doesn't
lose the last batch.

**Sampling: always on.** Every run is traced, the SDK's default (`parentbased_always_on`). At
this scale a trace costs a few rows, and a run you need to debug is never the one that was
sampled out.

**No auto-instrumentation.** We don't install the httpx, FastAPI or Anthropic instrumentation
packages, nor OpenInference's. They add spans we don't need, and some can record headers or
bodies, which is exactly where the delegated token and the provider key travel. Every span is one
we create, with attributes from the table below.

### The span tree

One trace per run. Its `trace_id` is stored on the run, which is how a run in the database is found
in Phoenix.

```text
invoke_agent pedro                       INTERNAL   one per run (the root)
├── execute_tool get_holdings            INTERNAL   a preloaded tool, before the first model call
├── chat <provider model>                CLIENT     step 1: one per model call
├── execute_tool get_performance         INTERNAL   a tool the model asked for
└── chat <provider model>                CLIENT     step 2
```

Names and kinds follow the GenAI spans conventions: `{gen_ai.operation.name}
{gen_ai.request.model}` for inference, `execute_tool {gen_ai.tool.name}` for tools, and
`invoke_agent {gen_ai.agent.name}` for an in-process agent.

- **The runner opens every span.** A model call is one `chat` span for the logical call. An adapter
  retry doesn't open a new span: the adapter reports each attempt's usage on the port (see "Token
  accounting"), each attempt gets its own `model_calls` row, and those rows share the span's id
  and differ by `attempt`. The span's usage attributes are the sum across attempts. This keeps
  the adapter free of tracing logic and keeps "which step spent the tokens" on one span.
- **Parallel tool calls** get sibling `execute_tool` spans under the run.
- **The root has no HTTP parent.** Without server instrumentation, the run span is the root.
  The request that started it is identified by `run_id` and `conversation_id`, which the API
  already logs.

### Attributes: GenAI conventions only

The GenAI conventions are **Development** status, not stable, and in 2026 they moved to their own
repository (`open-telemetry/semantic-conventions-genai`). They were still being renamed when this
was written: the old registry on opentelemetry.io names the cache-write count
`gen_ai.usage.cache_creation.input_tokens`, and the moved inference-span page names it
`gen_ai.usage.cache_write.input_tokens`. So **the attribute names are string constants in one
module in `observability/`**, not imports from `opentelemetry-semantic-conventions`' incubating
modules. A rename upstream is a one-file change.

**We send `gen_ai.*` only, not Phoenix's own OpenInference attributes.** Phoenix converts
`gen_ai.*` to OpenInference when spans arrive (since 15.10). The spike (see "Sources") sent the
span tree above with `gen_ai.*` attributes alone, and Phoenix showed everything we need: span
kinds, token counts with the cache split, tool names and ids, and error status. Sending the
OpenInference attributes as well changed nothing. So `agent` has no `openinference-*` dependency
and isn't tied to Phoenix: any OTLP backend reads the same spans. Phoenix keeps the original
`gen_ai.*` and `stakehold.*` attributes next to its converted ones.

| Attribute | Span | Value | Phoenix shows it as (spike) |
|---|---|---|---|
| `gen_ai.operation.name` | all | `invoke_agent`, `chat`, `execute_tool` | span kind `AGENT`, `LLM`, `TOOL` |
| `gen_ai.agent.name` | run, tool | persona name, e.g. `pedro` | kept as is |
| `gen_ai.conversation.id` | run, chat | conversation UUID | kept as is |
| `gen_ai.provider.name` | chat | the catalogue entry's provider, e.g. `anthropic` | `llm.provider`, `llm.system` |
| `gen_ai.request.model` | chat | the provider's model name | `llm.model_name` |
| `gen_ai.response.model` | chat | the model name the provider returned | kept as is |
| `gen_ai.response.id` | chat | the provider's message id | kept as is |
| `gen_ai.response.finish_reasons` | chat | our normalized stop reason, as a one-item array | `llm.finish_reason` |
| `gen_ai.usage.input_tokens` | chat | **all** input: `input + cache_read + cache_write` (see below) | `llm.token_count.prompt` |
| `gen_ai.usage.output_tokens` | chat | output, thinking included | `llm.token_count.completion` |
| `gen_ai.usage.cache_read.input_tokens` | chat | cache reads | `llm.token_count.prompt_details.cache_read` |
| `gen_ai.usage.cache_write.input_tokens` | chat | cache writes, all TTLs | `llm.token_count.prompt_details.cache_write` |
| `gen_ai.tool.name` | tool | tool name | `tool.name` |
| `gen_ai.tool.call.id` | tool | the provider's tool call id; absent for a preloaded tool | `tool.id` |
| `gen_ai.tool.type` | tool | `function` | kept as is |
| `error.type` | any | our typed failure reason, when the span failed | kept; status shown as `ERROR` |
| `stakehold.run.id` | run | run UUID | kept as is |
| `stakehold.step` | chat, tool | step number; 0 for preloads | kept as is |
| `stakehold.catalogue_model_id` | run, chat | e.g. `anthropic:<model>` | kept as is |
| `stakehold.cost_usd` | chat | our cost across attempts, as a decimal string | kept as is |
| `stakehold.pricing_version` | chat | catalogue pricing version used | kept as is |
| `stakehold.attempts` | chat | number of adapter attempts | kept as is |
| `stakehold.tool.preloaded` | tool | true or false | kept as is |
| `stakehold.outcome` | run | the `run_finished` outcome | kept as is |

**`gen_ai.usage.input_tokens` means something different from our column of the same name.** The
convention says it "SHOULD include all types of input tokens, including cached tokens", and the
cache counts are subsets of it. Anthropic's `input_tokens` is "only the portion after your last
cache breakpoint", and `agent`'s own `input_tokens` follows Anthropic's meaning (uncached input),
because each of the four counts has its own price. So the span attribute is the sum of three of
our columns, and the column is not the attribute. The spike confirmed Phoenix reads it as the
total prompt count with the cache counts as its parts.

`server.address` is left off: there's one provider endpoint per catalogue entry, and it's in the
catalogue.

**Status.** A span that failed has status `ERROR` and `error.type` set to our typed reason
(`provider_unavailable`, `rate_limited`, `tool_error`, `tool_refused`, `timeout`, …), never a
provider message. A tripped budget or a consent refusal is an *outcome*, not an error: the run
span's status is left unset and `stakehold.outcome` says what happened. A failed tool sets its own
span to `ERROR`, but the run carries on (the model is told), so the run span isn't failed by it.

### Content: never from production, opt-in for evals

The conventions define content attributes (`gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.system_instructions`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`). All of
them are **Opt-In**, with a warning that they're "likely to contain sensitive information".

- **Production (`agent serve`) never opts in.** Content is already stored once, in `messages`, in
  order, as the conversation. A span that needs to point at content stores the message id and the
  block index, not the text. Phoenix runs on our own infrastructure, but a second copy of every
  portfolio figure would need its own retention and deletion, and an exporter pointed elsewhere
  later would quietly send them off the box. Without content, a production trace in Phoenix
  still shows the structure, tokens, timings, tool names and outcome. To read what was said, you
  look the run up in `messages`.
- **The eval harness opts in.** Its worlds are synthetic by rule
  ([`synthetic-data-only.md`](../../agent-evals/.claude/rules/synthetic-data-only.md)), so there's
  nothing to protect, and a readable transcript is the point of the viewer. The SDK setup in
  `observability/` takes a `record_content` flag, false by default. Only the eval driver sets
  it, and the content attributes are written only when it's on.
- **The guard is a test.** The sentinel test under "Never logged, never traced" runs the runner as
  the API wires it, and asserts that no span attribute holds content.

In the spike, Phoenix turned the content attributes into its own message attributes
(`llm.input_messages.*`, `llm.output_messages.*`, a tool span's `output.value`) and kept the
originals as well, so an eval project holds each piece of content twice. That's fine for synthetic
data. Without content, Phoenix fills a `chat` span's `output.value` with only the response id and
model name.

### Phoenix deployment and configuration

- **Image:** `arizephoenix/phoenix`, pinned to an exact version tag (the spike used
  `version-22.0.1`). Upgrades are deliberate, like any other pin.
- **Where:** a `phoenix` container in `services/agent/docker-compose.yml`, on `stakehold-net`.
  `agent-api` reaches it there. Its UI and API port is published to the host on `127.0.0.1` only,
  for the developer. The gRPC OTLP port isn't used or published.
- **Storage:** `app-postgres`, under its own role and schema `phoenix`
  (`PHOENIX_SQL_DATABASE_URL`, `PHOENIX_SQL_DATABASE_SCHEMA=phoenix`). Phoenix's migrations own
  that schema's tables. They aren't `agent`'s schema and aren't in `agent/docs/schema.md`. The
  role and schema are created by the same admin-role step that creates `agent`'s. A fourth
  Postgres instance would be one more thing to run for data that's best-effort anyway.
- **Auth on:** `PHOENIX_ENABLE_AUTH=true`, with `PHOENIX_SECRET` and
  `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` from the environment, never the default
  `admin`/`admin`. The spike confirmed that with auth on, both OTLP ingest and the REST API answer
  401 without an API key. `agent-api` and the eval harness each send a Phoenix system API key in
  `OTEL_EXPORTER_OTLP_HEADERS`.
- **No outbound calls:** `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false` turns off its telemetry, fonts,
  update check and daily price fetch. `PHOENIX_TELEMETRY_ENABLED=false` and
  `PHOENIX_DISABLE_MODEL_COST_AUTO_UPDATE=true` are set too, so the intent is explicit.
  `PHOENIX_ENABLE_MCP_SERVER=false` turns off its built-in MCP server, which we don't use.
- **Phoenix's cost is informational.** Phoenix prices spans from the price table bundled in its
  image (a copy of LiteLLM's), stores a float, and doesn't know the cache-write TTL. In the spike
  it priced every cache write at the 5-minute rate, so a run with 1-hour writes shows too little.
  The authoritative cost is `model_calls.cost_usd`, which `agent usage` reports. Our cost is also
  on each `chat` span as `stakehold.cost_usd`.
- **Retention:** `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` defaults to 0, which means forever. We set
  a number of days; which number is an open question below.
- **Licence:** the server is under the Elastic License 2.0, which forbids offering it to others as
  a hosted service. Running it for ourselves is allowed. `agent` depends on none of Phoenix's
  packages; it only speaks OTLP to it.

## Token accounting

Every model call attempt records four provider-neutral counts, NOT NULL, 0 when the provider
lacks the concept:

| Column | Meaning | Anthropic `usage` field |
|---|---|---|
| `input_tokens` | uncached input | `input_tokens` |
| `output_tokens` | output, thinking included | `output_tokens` |
| `cache_read_tokens` | input read from the prompt cache | `cache_read_input_tokens` |
| `cache_write_tokens` | input written to the prompt cache, all TTLs | `cache_creation_input_tokens` |
| `cache_write_1h_tokens` | the part of `cache_write_tokens` written with the 1-hour TTL | `cache_creation.ephemeral_1h_input_tokens` |

Anthropic documents `total input = cache_read_input_tokens + cache_creation_input_tokens +
input_tokens`, and `cache_creation_input_tokens` as the sum of the `cache_creation` object's 5-minute
and 1-hour values.

**Why a fifth column.** The two cache-write TTLs are priced differently: a 5-minute write costs
1.25× base input and a 1-hour write 2×. A single `cache_write_tokens` can't be priced correctly
once a persona uses the 1-hour TTL. `cache_write_1h_tokens` is a *subset* of `cache_write_tokens`,
not a sibling, so the four counts keep their meaning, every SUM over them stays right, and a
provider with no TTL tiers just writes 0. The 5-minute part is `cache_write_tokens -
cache_write_1h_tokens`.

**Thinking tokens are output.** Anthropic: "the tokens Claude spends reasoning are billed as
output tokens, even when the thinking text isn't returned to you". With summarized thinking the
billed count is larger than what's visible, so `output_tokens` comes from `usage`, never from
counting streamed text.

**Where the counts come from.** The adapter fills a normalized `usage` event on the `ChatModel`
port. It's the only place a vendor usage type is seen. In a stream, Anthropic reports usage on
`message_start` and on `message_delta`, and "the token counts shown in the `usage` field of the
`message_delta` event are cumulative", so the adapter keeps the last values it saw, not a running
sum.

**Retries and failures.** The adapter retries transient errors itself. For the runner to record
each attempt, the port's `usage` event carries the **attempt number and the attempt's status**
(`ok`, `error`, `aborted`), and the adapter emits one for each attempt it made, including those
that failed. This is a requirement on PD-11's port types and PD-13's adapter:

- an attempt that failed before the provider reported any usage records zeros, with status
  `error`, so the row still shows the attempt happened;
- an attempt that failed or was cut off after streaming started records the last usage it saw,
  because those tokens can be billed;
- a client disconnect or a tripped budget ends the stream; the attempt is `aborted` with the last
  usage seen.

Anthropic doesn't document how usage is reported when a stream errors part-way. So for a
mid-stream failure the recorded count is a lower bound, and the doc for the usage report says so.

## Cost

`cost_usd` is computed once, when the attempt finishes, and stored with the pricing version used:

```text
cost_usd = ( input_tokens                              × input_price
           + output_tokens                             × output_price
           + cache_read_tokens                         × cache_read_price
           + (cache_write_tokens − cache_write_1h_tokens) × cache_write_5m_price
           + cache_write_1h_tokens                      × cache_write_1h_price ) / 1,000,000
           × residency_multiplier
```

- Prices are per million tokens, in the model catalogue, never in code. The catalogue carries a
  `pricing_version`, which each row snapshots. Past cost is never recomputed from today's prices.
- `Decimal` arithmetic and a `NUMERIC` column. Never a float: a fraction of a cent per call,
  summed over a month, is where float error shows up.
- **The catalogue's pricing must cover the dimensions the catalogue's models actually have.**
  Pricing isn't always one rate per token type. As of the pricing page read for this document:
  - Claude Haiku 5.5 charges more when the prompt is over 100,000 tokens, so its entry needs a
    prompt-length tier;
  - `inference_geo: "us"` applies a 1.1× multiplier to every token category; v1 sends no
    `inference_geo` (the default, global), so the multiplier is 1;
  - fast mode and the Batch API change prices too; v1 uses neither.
  PD-13 checks these against Anthropic's pricing page when it writes the catalogue, and the cost
  function covers whatever the catalogue's entries need. A dimension v1 doesn't use stays out of
  the code until a persona turns it on.
- **One function computes cost**, in `models/` next to the catalogue, from the normalized usage
  and the request settings that affect price. The runner uses it for the per-turn cost budget and
  the log lines, `observability/` stores its result, and the eval harness uses it for its cost
  ceiling. Three callers, one formula, so the budget, the report and the eval ceiling can't
  disagree.

## Recording consumption

`model_calls` rows are written as each attempt finishes, not at the end of the run. That's what
makes "nothing hides spend" survive a crash.

- **`observability/` exposes a `UsageRecorder` Protocol.** The runner calls it for each attempt
  with the run id, step, attempt, usage, cost, status, latency and the span's ids. Each driver
  passes its own implementation:
  - the API: a Postgres-backed recorder that commits each row in its own short transaction
    through the async pool;
  - the eval harness: an in-memory recorder that sums cost against the eval ceiling. Evals never
    write to `model_calls`, as the consumption rule says.
  This adds a dependency `runtime/ → observability/`'s Protocol, which the layering diagram
  already has. `runtime/` still knows nothing about Postgres.
- **A run row exists before its first model call.** It's inserted with status `running` in the
  same transaction that saves the user's message, then updated when the run ends. A
  `model_calls` row needs a run to belong to, and this gives it one.
- **A crash mid-run** leaves a `running` run with the calls it already paid for. On startup, `agent
  serve` marks runs left `running` for longer than the wall-clock budget as `failed` with reason
  `interrupted`, and logs how many it found. The spend stays recorded, and the turn shows as failed,
  not missing.
- **If a recorder write fails**, the run carries on (the user's answer shouldn't die because a
  bookkeeping insert failed), and the failure is logged as a `warning` with the run id. The
  `run_finished` line still carries the totals the runner counted, so the spend is visible in the
  log even if a row is missing.

## Rollups

All totals are SUMs over `model_calls`. No total is stored a second time.

- **Per run:** the run's rows.
- **Per conversation:** the rows of the conversation's runs.
- **Per user per day:** the rows with that `user_id`, grouped by `date_trunc('day', finished_at AT
  TIME ZONE 'UTC')`. **A day is a UTC day.** It's deterministic, needs no time-zone data `agent`
  doesn't have, and the first users don't need a local midnight. A call belongs to the day it
  *finished*, since that's when its cost is known.
- **Cache efficiency per run:** `cache_read_tokens / (input_tokens + cache_read_tokens +
  cache_write_tokens)`. A falling ratio after a prompt change means the change broke the cache
  prefix, which shows up here before it shows up on the bill.

The per-user daily budget (needed before wider access, see the architecture doc's "Failure
isolation and limits") is the per-user-per-day SUM of `cost_usd`, checked before each run. It
reuses the same query, so the budget and the report can't disagree.

## Consumption report

CLI first. A page in `web` can come later and read the same queries.

```text
agent usage [--user <uuid>] [--since <date>] [--until <date>]
            [--by day|user|conversation|run] [--format table|csv]
```

- Defaults: all users, the last 7 UTC days, grouped by day.
- Columns: the group key, runs, model calls, failed or aborted calls, the four token counts,
  cost (USD), and cache efficiency. `--by run` adds outcome and the catalogue model id.
- **The result goes to stdout and progress to stderr**, as in every other service's CLI, so
  `agent usage --format csv > usage.csv` works.
- **An empty window says so:** `no model calls between <since> and <until>` on stderr, exit 0. An
  empty table and a broken query must not look the same.
- A footer line on stderr names the window and the pricing versions the rows were priced with,
  and repeats that the cost of a stream that failed part-way is a lower bound.
- It reads through the sync `shared.db.pool`, since a CLI has no event loop to block.

## Never logged, never traced

| What | Logs | Spans | `model_calls` | How it's kept out |
|---|---|---|---|---|
| The delegated token | never | never | never | It lives only in `ToolContext` and the `PortfolioClient`'s header. No log line or span attribute takes a `ToolContext`; the client's errors don't include headers. |
| The session cookie | never | never | never | `agent` never sees it: `web` exchanges it for a delegated token. |
| Provider API keys | never | never | never | Read from the environment by the adapter's SDK client. No auto-instrumentation of the SDK or httpx; the OTel resource has only `service.name` and the version. |
| `AGENT_INTERNAL_SECRET` | never | never | never | Checked in an `api/` dependency, never logged. |
| Portfolio figures, tool arguments and results | never | never in production; synthetic ones in eval runs | n/a | Logs carry sizes, not content. Content attributes are written only when `record_content` is on, which only the eval driver sets. |
| User messages, prompts, model output | never | never in production; synthetic ones in eval runs | n/a | Stored in `messages` only. |
| Provider error messages | never verbatim | never | n/a | Adapters map them to typed errors (code and status); the text can echo the request. |
| The Phoenix API key | never | never | n/a | In `OTEL_EXPORTER_OTLP_HEADERS`, read by the SDK's exporter from the environment. |

Phoenix itself is a new place where trace data can be read. Its UI and API are behind its own
auth, published on `127.0.0.1` only, and make no outbound calls (see "Phoenix deployment and
configuration").

The user's UUID is logged (it's an opaque id, needed to answer "whose run was this"); their email
and name never reach `agent` in the first place.

**A test for PD-11 and PD-23:** run the runner as the API wires it (`record_content` off), with the
scripted fake model, a fake `PortfolioClient` that returns a sentinel figure, a sentinel user
message, and a `ToolContext` holding a sentinel token. Capture logs with `caplog` and spans with
the in-memory exporter, and assert that no sentinel appears in any log record or any span
attribute. A second case turns `record_content` on and asserts that the figure and the message
do appear but the token still doesn't, so the test can't pass by recording nothing.

## Fields PD-6's schema must hold

This is what the schema must be able to hold, not DDL. PD-6 designs the tables with the owner, per
[`data-schema.md`](../../../.claude/rules/data-schema.md).

**`runs`** (beyond what the architecture doc's "Persistence" already lists):

- `trace_id`, the OTel trace id (16 bytes): the link from a run to its trace in Phoenix;
- `status`: `running`, `completed`, `failed`;
- `outcome`, the typed value from `run_finished` (including `budget_exceeded:<which>`);
- `started_at`, `finished_at`;
- persona, catalogue model id, prompt hash (already listed);
- no token or cost totals.

**No `spans` table.** Spans live in Phoenix, under its own `phoenix` schema, which its migrations
own. Nothing in `agent`'s schema may require a span to exist: a span is exported later, or
dropped.

**`model_calls`:** one row per attempt:

- `id`;
- `run_id`, a foreign key to the run;
- `user_id`, denormalized from the conversation (see below);
- `trace_id`, `span_id`: the `chat` span in Phoenix, a plain reference with no foreign key;
- `step`, `attempt`;
- `catalogue_model_id`, `provider_model`;
- `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`,
  `cache_write_1h_tokens`: integers, NOT NULL, default 0, with a check that
  `cache_write_1h_tokens <= cache_write_tokens`;
- `cost_usd`: `NUMERIC`, NOT NULL;
- `pricing_version`, NOT NULL;
- `status`: `ok`, `error`, `aborted`;
- `stop_reason`, nullable;
- `latency_ms`;
- `started_at`, `finished_at`.

**Why `user_id` is denormalized onto `model_calls`.** The daily budget runs before every turn and
filters by user and day. Through `runs → conversations` it's a two-table join on the hot path.
A conversation's owner never changes, so the copy can't go stale. `user_id` is a trusted plain UUID
without a cross-database foreign key, as everywhere else in `agent`. Recommended index:
`(user_id, finished_at)`.

## Rejected alternatives

- **Our own `spans` table, filled by a custom `PostgresSpanExporter`.** That was this document's
  first draft. It needs no extra container, but it gives us rows with no way to read them except
  SQL, plus an exporter and a table to build and maintain. Phoenix stores the same spans in the
  same Postgres and adds the UI.
- **One table for spans and usage, with nullable usage columns.** Fewer tables, but the four
  counts would be NOT NULL only by convention, for one kind of row. Money columns should be
  enforced by the schema.
- **Our own span records, no OTel SDK.** That was the plan before PD-5, and it has fewer
  dependencies. The SDK gives us span ids, context propagation across `await`, and a tested
  processor, plus an exporter for Phoenix or any OTLP backend with no re-instrumentation. The cost
  is a dependency on packages that implement still-experimental conventions. The attribute
  constants in one module contain that.
- **Sending OpenInference attributes, or using OpenInference's instrumentation.** It's Phoenix's
  native format, but the spike showed Phoenix reads `gen_ai.*` just as well. Sending only the
  OTel conventions keeps `agent` free of Phoenix-specific packages and portable to another
  backend.
- **Phoenix's experiments and `arize-phoenix-evals` as the eval harness.** `run_experiment` would
  run our runner unchanged, with plain-function evaluators, and compare runs in the UI. But every
  eval would need the Phoenix server running, including the free smoke test and CI, and baselines
  would live in Phoenix's database instead of being reviewed in the repo. `arize-phoenix-evals` is
  also under the Elastic License. pydantic-evals stays the harness, and its runs send their spans
  to Phoenix. See the eval doc's "Harness choice".
- **DeepEval**, for evals or tracing. Apache-licensed, with many agent metrics, but at version
  4.3.5 it doesn't fit this workspace:
  - it loads `.env`, `.env.{APP_ENV}` and `.env.local` at import, which breaks the root conftest's
    no-dotenv guard and the evals' rule that they never read a `.env`;
  - it registers a `pytest11` plugin, so it loads in every pytest run in the workspace, and
    it requires pytest-xdist, pytest-repeat and pytest-rerunfailures;
  - it adds about 80 packages to the shared lockfile, against pydantic-evals' 23;
  - its telemetry to PostHog is on by default;
  - marking a run as the regression baseline (`--official`) needs its hosted Confident AI service;
  - almost all its built-in metrics are LLM-judged, so each one is a paid call.
- **Langfuse**, another self-hostable trace backend. Not evaluated in depth. It would take the same
  OTLP spans, so switching later is a deployment change, not re-instrumentation.
- **Writing `model_calls` from the span exporter.** One write path, but a lossy one. Spend must not
  depend on a queue that drops.
- **Writing `model_calls` once, at the end of the run.** Simpler, but a crash loses the spend
  of a run that was already paid for.
- **JSON logs.** Better for an aggregator we don't have, and different from every other service.
- **The user's local day.** Matches what users mean by "today", but `agent` has no time zone for
  them, and the budget would need one before it could run.

## Open questions

- **Span retention in Phoenix.** Spans are debugging data and can be kept for less time than
  conversations. The proposal is 30 days for the `agent` project and 90 for `agent-evals`, set
  through Phoenix's per-project retention policies. It goes with the architecture doc's open
  retention question.
- **Phoenix's annotation UI for the voice judge's calibration set (PD-18).** Labelling eval
  transcripts in Phoenix might be easier than a hand-edited file. Decide when PD-18 starts.
- **Tier-3 online evals** (see the eval doc) would read a run's content from `messages` and its
  structure from Phoenix. Whether they need anything more is decided when tier 3 is designed.

## Sources

Checked on 2026-11-24. Re-check them when building, since the GenAI conventions and the pricing
both change:

- OpenTelemetry GenAI semantic conventions, `open-telemetry/semantic-conventions-genai`:
  `docs/gen-ai/client-inference.md`, `execute-tool.md`, `gen-ai-agent-spans.md`; the attribute
  registry at opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/.
- OpenTelemetry trace SDK spec (batching processor), trace API spec (no-op without an SDK), SDK
  environment variables; the Python SDK's `opentelemetry.sdk.trace.export` reference.
- Anthropic docs: prompt caching, pricing, streaming, extended thinking (platform.claude.com).
- Phoenix docs (arize.com/docs/phoenix): self-hosting configuration, authentication, privacy,
  data retention, cost tracking, and the 2026-05-15 release note on converting OTel GenAI
  attributes. The server's licence is in the `Arize-ai/phoenix` repository.
- **Phoenix spike, 2026-11-24**, with no model calls and no code kept. It ran
  `arizephoenix/phoenix:version-22.0.1` locally with outbound access off. It sent the span tree
  above over OTLP HTTP from `opentelemetry-sdk` on Python 3.14, three ways: `gen_ai.*` only,
  `gen_ai.*` plus OpenInference attributes, and `gen_ai.*` plus content. Findings:
  - `gen_ai.*` alone produced span kinds `AGENT`, `LLM` and `TOOL`, the token counts with the
    cache read/write split, tool names and ids, and `ERROR` status. Adding OpenInference attributes
    changed nothing.
  - The `openinference.project.name` resource attribute picked the project.
  - Content attributes were converted into Phoenix's message attributes, and the originals were
    also kept.
  - Phoenix priced the spans from its bundled table as floats, with every cache write at the
    5-minute rate.
  - With auth on, OTLP ingest and the REST API both returned 401 without an API key.
- DeepEval: the PyPI metadata and `pyproject.toml` for 4.3.5 (dependencies, the `pytest11` entry
  point), and the deepeval.com docs on environment variables, data privacy and local storage.
- Dependency counts were resolved on Python 3.14 with `uv pip compile`: deepeval 80,
  arize-phoenix 143, arize-phoenix-otel 23, arize-phoenix-evals 19, pydantic-evals 23. Each
  resolved against the workspace's locked versions.
