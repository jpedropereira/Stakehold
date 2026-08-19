# Observability / Logging

Every script, CLI command, or long-running/multi-step operation must make its progress and
outcome visible without requiring the reader to query a database, inspect return values, or
guess from silence.

## What to emit

- **Start**: what's about to happen and its scope (how many items/records targeted).
- **Checkpoints**: for operations that take non-trivial time, one line per natural unit of work
  (batch, group, item in a slow loop) — skip this for near-instant operations.
- **Completion summary**: counts of what happened (processed, saved, skipped, failed). A run
  that did nothing must say so as clearly as a run that did a lot.
- **Distinguish "legitimately nothing to do" from "something went wrong silently"** — these must
  never look the same in the output.

## Mechanism

The right visibility mechanism (print vs. structured logging, log level, destination) depends on
how each service actually runs and where its output ends up captured. Don't assume `logger.info`
(or any log call) is visible anywhere without first confirming a handler is actually configured
to show it — check the service's own `.claude/rules/logging.md`, if one exists, for the concrete
convention before adding new output.

## Testing

Progress/summary output is observable behavior — test it the same way you'd test a return value,
using whatever output-capture mechanism the language/framework provides (e.g. pytest's `capsys`).
