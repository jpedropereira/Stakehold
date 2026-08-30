# shared architecture

## What this is

Cross-service infrastructure, and nothing else: a thin Postgres connection/transaction/schema-apply
helper and log setup, factored out after `auth` and `data-store` each ended up with a byte-for-byte
copy of the same code. Not a service — no entry point, no CLI, no schema, no domain logic. Every
other workspace member (`data-store`, `auth`, and `portfolio` once it needs a database) depends on
this; it depends on nothing workspace-internal.

## What it owns

- `db/connection.py` — `get_connection`, `transaction`, `apply_schema`. The psycopg connection
  lifecycle every service's `jobs.py` needs: opening a connection, committing or rolling back one
  unit of work, and applying a schema file. None of it is domain-specific — the same operations
  apply whether the schema being applied is `auth`'s or `data-store`'s.
- `logging_config.py` — `configure_logging`. Installs the one stderr handler every entry point
  needs so `logger.info` isn't silently dropped (Python's default `lastResort` handler only emits
  WARNING and above), and keeps the "results on stdout, everything else on stderr" convention
  consistent across services.

## What it deliberately does not own

- **Schema DDL and its meaning.** Each service keeps its own `db/schema.sql` and
  `docs/schema.md` — [`data-schema.md`](../../../.claude/rules/data-schema.md)'s ownership rule
  applies per service, not to this package. `apply_schema` here only knows how to run a SQL file
  inside a transaction; it has no opinion on what the file contains.
- **`data_store.run_result`'s status/rows_saved contract.** That module is deliberately not here:
  it is shared vocabulary between exactly `data_store`'s own layers (pipeline, jobs, CLI, Airflow
  XCom), not yet something a second service's job needs to interoperate with the same way. Move it
  here if a second service needs the same `ok`/`nothing_to_do`/`partial` distinction and the two
  independent implementations start drifting — not before, since generalizing it now would mean
  guessing at a shape a second real caller hasn't asked for yet.
- **Business logic, domain modules, or an entry point of its own.** No `jobs.py`, no `cli.py`, no
  `[project.scripts]`. If this package ever needs one, it has stopped being "shared infrastructure"
  and the thing that needs it belongs in its own service instead.

## Why extracted now

`auth`'s schema/foundation task (task 1 in `tasks.md`) copy-pasted `data-store`'s
`db/connection.py` and `logging_config.py` near-verbatim to stand up its own `db init`. A second
copy is a fork the instant one needs a fix: `apply_schema` not routing through `transaction()` —
so a failed statement left the connection aborted with no rollback — was a real bug caught in
`auth`'s copy and had to be fixed in both places by hand. Extracted on the second occurrence
rather than the first: one copy can be coincidence, two is a pattern, and `portfolio` standing up
its own database would have made it three.

## Consumers

- `data_store.jobs` and its `market_data`/`returns`/`regimes`/`fx` repositories — `transaction`.
- `data_store.jobs` and `auth.jobs` — `get_connection`, `apply_schema`, `configure_logging`.

## Deployment

Not independently deployed — a workspace-only library, resolved through `uv`'s
`[tool.uv.sources]` `workspace = true` mapping like every other inter-service dependency here (see
each consuming service's own `pyproject.toml`). Any service whose image bakes its own code in at
build time — currently only `data-store`'s `Dockerfile.airflow` — must also `COPY` this package
into the build context, since `uv sync` inside the image needs it present to resolve the workspace
lock.
