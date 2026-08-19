# Architecture Documentation

## Scope

- Every service documents its own architecture at `services/<service>/docs/architecture.md` —
  data-store, portfolio, or any future service.
- A new service gets that file alongside its first non-trivial code, not once it has grown
  complicated enough to need one.

## Architectural Changes

An architectural change in a service requires reviewing `services/<service>/docs/architecture.md`
and updating it **as part of the same change — not as a follow-up.**

Treat a change as architectural if it does any of these:

- Adds, removes, renames, or splits a module or package within the service.
- Moves a responsibility across a layer boundary (e.g. orchestration logic into a domain module,
  or persistence logic out of one).
- Adds or changes an extension point — a registry, a `Protocol`, a plugin surface, a base class
  others are expected to implement.
- Adds or removes an entry point: a CLI command, a DAG, an API route, a scheduled job.
- Changes how components communicate — in-process call vs subprocess vs shared table vs queue.
- Adds or removes an external system the service talks to (a data provider, a third-party API).
- Adds, removes, or re-wires an infrastructure component: a container, a database, a broker, or
  the topology connecting them.
- Changes the configuration surface: a new config file, a change in which file owns what, or a
  new environment variable that changes behaviour rather than just credentials.
- Changes a transaction, failure-isolation, or retry boundary.
- Changes the build/deploy shape — in particular what is baked at build time versus mounted or
  read at runtime.

Not architectural, and not a reason to touch the file:

- Bug fixes that leave responsibilities where they were.
- Adding tests.
- Adding an entry to an existing mapping or config table that the docs already describe generically.
- Performance tuning inside a single component.
- Comment or documentation-only edits.

## How to Update

- Record the **why**, not the what. The code already shows the structure; the document exists for
  the reasoning, the constraints, and the alternatives that were rejected.
- If a change makes an existing statement in the file false, fix that statement. A stale
  architecture document is worse than no architecture document, because it is trusted.
- If a change introduces a known limitation or sharp edge, say so plainly and name the intended
  fix. Do not describe the intended design as though it were the current one.
- Update the diagrams too, not just the prose.
- Verify by reading the file. Concluding "this change needs no doc update" is a valid outcome, but
  only after checking — never by assumption.

## Related

- Table and column changes are governed by `data-schema.md`, which owns
  `services/<service>/docs/schema.md`. This rule covers the structure around the data; that one
  covers the data itself. A change can easily trigger both.
