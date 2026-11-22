# Conversation recording (agent)

Every conversation is recorded. A turn that happened and left no record can't be explained,
debugged or turned into an eval case later, and it would look the same as a turn that never
happened.

## Every turn is recorded, whatever the outcome

- The user's message is saved **before** the run starts.
- The assistant's message and the run record are saved when the run ends, whatever the outcome:
  answered, failed, budget tripped, consent refused, or client disconnected.
- A crash mid-run leaves a visible failed or incomplete run, never a silently missing turn.

## Record what happened, in order

Messages hold ordered content blocks (text, tool call, tool result, action proposal), mirroring the
normalized `Message` type. Partial assistant text from an aborted stream is kept and marked
incomplete, not dropped.

## Record enough to explain an answer

Each run records the catalogue model id and adapter, the prompt hash and version, its status and
failure reason. Any answer can then be traced to the prompt and model that produced it.
Consumption is recorded per model call (see `consumption.md`).

## In the database, not the logs

- Conversation content goes to the `agent` schema only.
- Container logs carry ids, counts and statuses, never message or tool-result content, which holds
  portfolio figures.
- The delegated token, cookies and provider API keys are never recorded anywhere.

## Who writes it

`conversations/` owns the record: its repository only executes SQL, and its service owns the
transaction. `runtime/` never touches the database. It yields `AgentEvent`s, and the API side
persists them.

## No silent opt-outs

No code path (a new endpoint, a fast path, a retry) may run a model call without a recorded run and
its messages. Evals are the one exception: they drive the runner in process and write reports,
never to the production tables.

## Not decided yet: don't decide it in code

These are open questions in `docs/architecture.md`. Ask the owner rather than picking a default:

- how long conversations are kept;
- whether a user can delete a conversation, and whether that is a hard or soft delete;
- what deletes a user's conversations when the user is removed.

Schema changes go through the root `data-schema.md` sign-off and are documented in
`services/agent/docs/schema.md` in the same change.
