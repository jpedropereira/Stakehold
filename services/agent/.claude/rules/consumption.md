# Consumption (agent)

Every model call records its usage and cost. Nothing hides spend: not a retry, not a failed call,
not a budget trip.

## One record per model call

Each call to a `ChatModel`, including each retry the adapter makes, is one row in a dedicated
table (working name `model_calls`), owned by `observability/` and a child of the run. A turn with
N tool steps writes at least N+1 rows. Don't fold calls into a per-run total: it hides which step
spent the tokens and whether the prompt cache hit.

## Four typed token counts

Every row has these, NOT NULL, with 0 when the provider lacks the concept:

- `input_tokens`: uncached input;
- `output_tokens`: includes thinking tokens, since they're billed as output;
- `cache_read_tokens`;
- `cache_write_tokens`.

A fifth, `cache_write_1h_tokens`, is the part of `cache_write_tokens` written with the 1-hour TTL,
since it's priced differently from a 5-minute write. It's a subset, not a fifth kind, so SUMs over
the four stay right.

They are `agent`'s own provider-neutral fields, filled by the adapter from the `usage` event on the
port. Don't add a vendor-shaped JSON blob in their place, and don't leak a vendor usage type past
the adapter.

## Cost is snapshotted when the call finishes

`cost_usd` is the four counts times the model catalogue's per-token pricing, stored with the
catalogue/pricing version used. Never recompute past cost from today's prices: a price change must
not rewrite history. Pricing lives in the catalogue config, not in code.

## Failed and aborted calls are recorded too

Each row has a status (`ok`, `error`, `aborted`). A call that errors after streaming, a client
disconnect and a tripped budget can all bill tokens, so record whatever usage the provider
reported, whatever the outcome.

## Written by the service, never from spans or Phoenix

`observability/`'s service writes each row as the attempt finishes, through the `UsageRecorder`
the runner is given. The OpenTelemetry batch processor drops spans when its queue is full or
Phoenix is down, so spend never goes through it. Don't derive consumption from spans or from
Phoenix's cost figures (they're floats, priced from Phoenix's own table), and don't wait for the
run to end to write it: a crash would lose calls already paid for.

## Totals are derived

A run's tokens and cost are the SUM of its calls. A per-user daily budget is a SUM over a day's
rows, where a day is a **UTC** day, by the call's `finished_at`. Don't store the totals a second
time.

The full design, including the 1-hour cache-write column and the fields the schema must hold, is in
[`../../docs/observability.md`](../../docs/observability.md).

## Make it visible

Per the root `logging.md`: one checkpoint line per model call (the four counts, cost, latency,
status), and a completion summary per run (steps, tokens, cost, outcome). A run that stopped on a
budget says so as clearly as one that answered. Never log prompt or tool-result content (see
`prompt-injection.md`).

## Schema

The table's DDL isn't designed yet. Don't create it without the user's sign-off, and document it
in `services/agent/docs/schema.md` in the same change (root `data-schema.md`).

Evals don't write to this table. They read the usage the runner reports in-process, against the
eval cost ceiling.
