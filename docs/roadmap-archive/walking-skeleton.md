# Walking skeleton roadmap (archived)

Completed 2026-10, tasks 1–15. This is the original `tasks.md`, moved here when `tasks.md` was
repurposed for the next roadmap chunk (UI/UX). The content is verbatim except for relative links,
adjusted for the new location. References elsewhere in the repo to "walking-skeleton task N"
point at the subsections below.

---

# Tasks

Roadmap to a basic, working walking skeleton across all four services: a user registers and logs
in (`auth`), manually enters or uploads their portfolio (`portfolio`), and views it by account and
by value/returns in a basic web UI (`web`), with prices/FX sourced from `data-store`.

Each task implements what's already specified in that service's `docs/architecture.md` (and
`docs/schema.md` where one exists) — this file only sequences the work and tracks status. Update
the status table and the per-task notes as work completes; this is a living tracker, not a
one-time snapshot.

**Status** is one of:
- `done` — implemented, tested, merged.
- `ready` — every dependency is `done`; can be started now.
- `blocked` — waiting on at least one dependency.

## Status table

| ID | Task | Depends on | Status |
|----|------|-------------|--------|
| 1 | [Auth schema & foundation](#1-auth-schema--foundation) | — | done |
| 2 | [Auth identity API](#2-auth-identity-api) | 1 | done |
| 3 | [Data-store read API](#3-data-store-read-api) | — | done |
| 4 | [Portfolio foundation & schema sign-off](#4-portfolio-foundation--schema-sign-off) | — | done |
| 5 | [Accounts & manual transactions](#5-accounts--manual-transactions) | 4 | done |
| 6 | [Broker import](#6-broker-import) | 5 | done |
| 7 | [Positions](#7-positions) | 6 | done |
| 8 | [Returns](#8-returns) | 7, 3 | done |
| 9 | [Portfolio HTTP API](#9-portfolio-http-api) | 5, 6, 7, 8 | done |
| 10 | [Web foundation & auth pages](#10-web-foundation--auth-pages) | 2 | done |
| 11 | [Portfolio entry UI](#11-portfolio-entry-ui) | 10, 9 | done |
| 12 | [Portfolio view UI](#12-portfolio-view-ui) | 11 | done |
| 13 | [End-to-end integration & smoke test](#13-end-to-end-integration--smoke-test) | 12 | done |
| 14 | [Broker cash-ledger import](#14-broker-cash-ledger-import) | 6 | done |
| 15 | [Extract broker-parser plugins to a private repo](#15-extract-broker-parser-plugins-to-a-private-repo) | 6, 14 | done |

No task is `ready` today — every dependency chain resolves to `done`.

## Tasks

### 1. Auth schema & foundation — done

`services/auth/docs/schema.md` written and signed off per
[`data-schema.md`](../../.claude/rules/data-schema.md): `users` (with `display_name`) and `sessions`
(opaque token stored only as a `SHA-256` hash, plus a fixed `absolute_expires_at` cap alongside
sliding renewal — both decisions recorded there and in `architecture.md`). Implemented:
`settings.py` (`AUTH_DATABASE_URL`, not the bare `DATABASE_URL` data-store owns), `db/schema.sql`,
`jobs.py`/`cli.py` (`auth db init`), dedicated `auth-postgres` in `docker-compose.yml` (isolated
from `app-postgres`/`data-store-postgres`), CI service container + `AUTH_DATABASE_URL` wiring. The
connection/transaction helper and log setup underneath moved to `services/shared`, a workspace
package `data-store` also depends on, once `auth`'s copy of both turned out to be near-identical to
`data-store`'s own — see `services/shared/docs/architecture.md`. Unit tests (`settings`, `cli`,
`jobs`) and integration tests (`db init` idempotency, email uniqueness, cascade delete, token-hash
uniqueness)
under `services/auth/tests/`.

### 2. Auth identity API — done

FastAPI app (`src/auth/api/`) exposing `POST /signup`, `POST /login`, `POST /sessions/validate`,
`POST /logout`, `GET /health`. Argon2id password hashing (`passwords.py`, `argon2-cffi`), opaque
session tokens (`tokens.py`, SHA-256-hashed at rest). `auth` sets the session cookie itself
(`api/cookies.py`: `httpOnly`, `Secure`, `SameSite=Lax`) rather than handing `web` a raw token;
sliding renewal on validation, clamped to the fixed `absolute_expires_at` cap
(`sessions/repository.py`). `psycopg_pool.ConnectionPool` opened in the FastAPI lifespan
(`shared.db.pool`, new in this task) rather than a connection per request. Shared-secret check
(`X-Internal-Secret`) on every route except `/health`. Also shipped: in-process sliding-window
throttles on both login failures and signup attempts (`throttle.py`, `AttemptThrottle` — per-worker,
resets on restart, see `docs/architecture.md`'s open questions), user-enumeration defence
(`passwords.dummy_verify`), and `auth sessions purge-expired` (`cli.py`/`jobs.py`) for the
expired-session sweep. Signup's user and session rows are written in one transaction, owned by the
service rather than the repositories (`docs/architecture.md`, "Transaction boundaries"). Compose
runs `auth db init` as a one-shot `db-init` service the API waits on, and `/health` reports `503`
unless the schema is actually there. Unit tests (`tests/unit/`, fake repositories
for the service/API layers) and integration tests (`tests/integration/`, live Postgres: full
signup→validate→logout round trip, duplicate-email handling, sliding-renewal and
absolute-cap-clamping behaviour, the purge job).

### 3. Data-store read API — done

A FastAPI app (`services/data-store/src/data_store/api/`) exposing `GET /prices/{ticker}`
(`market_returns`), `GET /fx/{base}/{quote}` (`fx_rates`, deriving direct/inverse/triangulated
pairs through `config/currencies.yaml`'s base currency), `GET /tickers/{ticker}` (existence +
whether returns are computed), and an unauthenticated `GET /health`. Shared-secret protected —
`Authorization: Bearer <INTERNAL_API_SECRET>` — matching the trust model
`auth/docs/architecture.md` describes. New `read_flows.py` (a sibling of `pipeline.py`) and three
`jobs.get_*` functions carry the read-side orchestration; `settings.py` gained an optional
`internal_api_secret`. Wired into `docker-compose.yml` as its own `api` container
(`Dockerfile.api`). `services/data-store/docs/architecture.md`'s "Read API" section covers the
design, including the FX-derivation rules and known limitations (per-request connections,
`calculated_at` not being a freshness signal). Unit tests (`tests/unit/api/`,
`tests/unit/test_read_flows.py`, plus additions to the returns/fx repository and jobs tests) and
an integration suite (`tests/integration/test_db_read_api.py`) against live Postgres.
`services/portfolio/docs/architecture.md`'s cross-service-prerequisite section is corrected to
match (FX is not modeled as a yfinance-style ticker) and updated to point at the now-built API;
writing `datastore_client/http_client.py` against it remains task 8's own work, not a blocker on
this task.

### 4. Portfolio foundation & schema sign-off — done

`services/portfolio/docs/schema.md` signed off per
[`data-schema.md`](../../.claude/rules/data-schema.md) and implemented as `src/portfolio/db/schema.sql`
(`accounts`, `transactions`, `position_snapshots`, `return_snapshots`, every statement guarded so
`portfolio db init` is safe to rerun). One amendment at sign-off: `transactions.broker` was
dropped and the idempotent-import key narrowed to `UNIQUE (account_id, broker_transaction_ref)` —
an account has exactly one broker, so the column was functionally dependent on `account_id` and
could only ever disagree with it. Implemented: `settings.py` (`PORTFOLIO_DATABASE_URL`, not the
bare `DATABASE_URL` data-store owns), `run_result.py` (data-store's `{"status", "rows_saved"}`
contract, replicated not imported), `jobs.py`/`cli.py` (`portfolio db init`), `app-postgres` in
`services/portfolio/docker-compose.yml` (port 5435, isolated from `data-store-postgres` and
`auth-postgres`), `.env.example`, CI service container + `PORTFOLIO_DATABASE_URL` wiring. Unit
tests (`settings`, `run_result`, `cli`, `jobs`) and integration tests (`db init` idempotency,
per-user label uniqueness, account-scoped import idempotency, FK enforcement, snapshot keys)
under `services/portfolio/tests/`.

**Two deliverables named in the original scope were dropped, deliberately.** `db/connection.py`:
that code moved to the `shared` workspace package during task 1, so portfolio imports
`shared.db.connection` rather than becoming a third copy. `pipeline.py`: `db init` doesn't route
through it in either existing service, and a file holding only a logger is dead code — task 5's
`account add` is the first flow that needs one, and creates it.

### 5. Accounts & manual transactions — done

`accounts/repository.py` (the multi-user isolation chokepoint — every read takes a `user_id`, and
there's deliberately no unscoped `get_by_id`, only `get_by_label`/`list_by_user`),
`transactions/base.py` (the `CanonicalTransaction` shape, plus a pure `validate()` and `to_eur()`)
+ `repository.py` (idempotent insert, `ON CONFLICT ... DO NOTHING`). New `pipeline.py` — the flow
layer `architecture.md` described but task 4 deliberately hadn't built yet — owns the
`with transaction(conn):` boundary and a placeholder `KNOWN_BROKERS` tuple standing in for task 6's
`BROKER_PARSERS` registry. New `errors.py` (`PortfolioError` and subclasses) so `jobs.py` can catch
every domain failure with one `except`. CLI flows: `account add --user --broker --label`,
`account list --user` (not originally scoped, added because `--account` takes a user-chosen label
a user needs a way to see), `add-transaction` (`--ref` mandatory, making manual entry idempotent by
construction; `--amount` always required, `--fx-rate` required for non-EUR with `eur_amount`
derived locally). Two corrections to `architecture.md`'s originally-documented
`CanonicalTransaction` shape, made while implementing it: `account_id` is `int` (the resolved
`accounts.id`), not `str`; the `broker` field is dropped (schema.md already dropped the
functionally-dependent `transactions.broker` column at sign-off, task 4). One schema-*document*
amendment, not a DDL change: `fx_rate_source` gains `'manual'` for a user-typed rate. Tests (unit:
pure logic plus pipeline orchestration against fake repositories; integration: idempotent-import
and multi-user-isolation semantics against live Postgres) under `services/portfolio/tests/`.

### 6. Broker import — done

`ticker_mapping/loader.py` (reads/validates `configs/ticker_mappings.yaml`; `pyyaml` added as a
portfolio dependency). `broker_import/base.py` (the `BrokerParser` Protocol) + two broker-specific
parser modules, both stdlib `csv`/`hashlib`/`zoneinfo` — no pandas. `pipeline.py`'s
`BROKER_PARSERS` registry replaces task 5's placeholder `KNOWN_BROKERS` tuple. Flows:
`import-transactions --user <id> --broker <key> --file <path> --account <label>`,
`list-unmapped-instruments --user <id>`. New `errors.py` subclasses: `BrokerMismatch`,
`BrokerFileError`, `TickerMappingError`. New `transactions/repository.py` read:
`list_distinct_instruments`, joined to `accounts` for both `user_id` isolation and the owning
broker (SYMBOL mappings are broker-scoped).

**Several corrections to `docs/architecture.md`, made while parsing real export files
(`dev/porfolio/`, gitignored) from the two initially-supported brokers, rather than assumed from
the documented formats** — including a broker-reported id turning out not to be row-unique (a
partial fill split across two rows sharing one id, resolved by hashing row content plus an
occurrence index instead), and a documented FX-design risk that turned out not to apply once a
real export was available to check against. See task 15: the exact real-data findings, and which
broker each applies to, now live in the private plugin package's own docs, not here.
`import-transactions --account <id>` in the original flow table couldn't be built as documented —
`accounts/repository.py` deliberately has no unscoped `get_by_id` — so it takes `--account
<label>`, resolved via `get_by_label` like manual entry.

**One scope gap surfaced, not silently absorbed**: one broker's trade export carries trades
only — deposits, withdrawals, and dividends live in that broker's separate cash-ledger export,
unparsed by this task. See task 14.

Tests (moved to the private plugin package at task 15): fixture-file-backed parser tests against
real, trimmed exports, including the content-hash dedup logic and a shared-natural-key
partial-fill case; `tests/unit/ticker_mapping/` (stayed here — genuinely broker-agnostic);
`tests/integration/test_broker_import.py` (idempotent re-import, `list_distinct_instruments`
multi-user isolation, against live Postgres — stayed here too, rewritten at task 15 to exercise a
synthetic parser instead of a real one).

### 7. Positions — done

`positions/calculator.py` (pure function replaying the transaction log) + `repository.py`
(delete-then-insert per `(account_id, canonical_ticker)`, each pair's replacement wrapped in its
own `with transaction(conn):` opened by `pipeline.rebuild_positions`, not by the repository —
`schema.md`'s atomicity requirement is per-pair, not per-run). Flow: `rebuild-positions --user <id>
[--account <label>]`, resolved via `AskUserQuestion` — the original flow table showed the command
bare, with no flags. New read on `transactions/repository.py`: `list_by_accounts`, fetching every
relevant account's transaction log in one batched round trip rather than one query per account
(`list_by_account` is a thin single-account wrapper over it) — every prior read there was either
`user_id`-scoped or a write. An unmapped instrument blocks only its own ticker, reported by name
with a `partial` status, never a silent skip — every other `(account, ticker)` in the run still
gets rebuilt. A driver-level failure on one `(account, ticker)` pair (as opposed to a
`PortfolioError`) is caught at that same per-pair transaction boundary, logged, and reported in
`failed_pairs` rather than aborting every account still left to process — see
`docs/architecture.md`'s "A rebuild's own failure isolation."

**Three decisions left open by both `architecture.md` and `schema.md`, settled here and recorded in
both**: cost basis is sourced from `native_amount`/`eur_amount` (the broker's own frozen cash-flow
figures), not `quantity * price` — so `average_cost_eur` needs no FX arithmetic in the calculator at
all; only `BUY`/`SELL` move a position, since `validate()` guarantees neither `DIVIDEND` nor `FEE`
nor `ADJUSTMENT` carries a quantity or price; a ledger anomaly (the same ticker traded in two
currencies within one account, or a running quantity going negative) warns but still computes,
reserving `partial` for unmapped instruments alone. A related note added to `schema.md`: the same
instrument held in two *different* accounts, each in its own currency, is ordinary and fully
supported — each account replays independently, and `average_cost_eur` is a blend of each
purchase's own frozen `eur_amount`, not derivable from `average_cost_native` by any single
`fx_rate`.

**One piece beyond the task's literal wording, added deliberately**: `positions/repository.py`
gained `list_tickers`/`delete_snapshots` so a rebuild prunes a ticker it no longer produces (e.g. a
mapping in `configs/ticker_mappings.yaml` was re-pointed) — otherwise a "full rebuild" would leave
stale rows behind forever. Pruning is skipped for any account with a blocked instrument, since a
blocked instrument's old `canonical_ticker` is indistinguishable from a genuinely stale one at
rebuild time, and "blocked means don't touch" must not become "blocked means delete."

Tests: `tests/unit/positions/{test_calculator,test_repository}.py` (the calculator suite is the
bulk — average-cost blending, sell-to-zero reset, re-buy after close, both anomaly cases, and the
two-account/two-currency independence case guarding against a same-instrument mix-up); additions to
`tests/unit/{test_pipeline,test_jobs,test_cli}.py` (including the batched-fetch call count, a
non-BUY/SELL row never reaching the mapping check, and a driver-level failure on one pair being
isolated and reported rather than aborting the run) and
`tests/unit/transactions/test_repository.py`; `tests/integration/test_rebuild_positions.py` (live
Postgres, seeded through the real CLI/pipeline stack — the delete-then-insert replacement contract a
fake can't prove, `--account` scoping, the whole-portfolio case rebuilding every account via the
batched fetch, per-user isolation, an unmapped instrument blocking with exit code 1, and a bad
`--account` label exiting cleanly rather than crashing).

### 8. Returns — done

`datastore_client/http_client.py` extended with `get_price_series`, `get_fx_rate_series`, and
`get_ticker_info` (`GET /prices/{ticker}`, `GET /fx/{base}/{quote}` widened to a range, `GET
/tickers/{ticker}`), alongside `get_fx_rate` (task 14, untouched). `get_fx_rate_series` was built
as a new sibling method rather than reusing single-date `get_fx_rate` per priced day — one HTTP
call per `(account, ticker)` pair needing FX conversion, not one per date, given a rebuild can
span years of daily prices. New `returns/calculator.py` (pure `compute_returns`, no I/O) +
`returns/repository.py` (`upsert_snapshots`, a true upsert matching `market_returns`'s loading
strategy). New `positions/repository.py` read: `list_snapshots(account_id, canonical_ticker)`,
the position's full history — needed to know quantity/cost basis as of each priced day. New
`pipeline.rebuild_returns` / `jobs.run_rebuild_returns` / `rebuild-returns --user <id> [--account
<label>]`, mirroring `rebuild_positions`'s shape: a ticker data-store doesn't track (or has no
computed returns for) blocks only that `(account, ticker)` pair, loudly, via a new
`get_ticker_info` pre-check cached per ticker for the run; a `DataStoreApiError` (or any other
failure) fetching or saving one pair is caught and reported in `failed_pairs` rather than
aborting the run — deliberately broader than `rebuild_positions`'s own per-pair catch, since this
is the first rebuild flow with an HTTP dependency.

**One design decision the original scope explicitly left open, settled during implementation**:
whether to widen `get_fx_rate` to a range signature or add a new sibling method. Went with a new
`get_fx_rate_series`, leaving `get_fx_rate` untouched for `import-cashflow`'s existing single-date
use — see `docs/architecture.md`'s cross-service prerequisite section for the full reasoning.

**A gap in the original design surfaced during planning, requiring a schema amendment, not just
new code**: as originally signed off, `return_snapshots` computed only mark-to-market return
(`unrealized_return_eur`) with no path for `DIVIDEND` transactions at all — contradicting
`docs/architecture.md`'s own task-7 note that cash events are "left entirely to `returns/`."
Total return is price change plus dividends received, so three columns were added to
`return_snapshots` — `dividends_received_eur`, `total_return_eur`, `total_return_pct` — proposed
and signed off per [`data-schema.md`](../../.claude/rules/data-schema.md) before being written.
`unrealized_return_eur`/`unrealized_return_pct` are unchanged, kept as the pure price-only
figures; `db/schema.sql` carries both the updated `CREATE TABLE` (for a fresh database) and an
idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (to migrate a database created during tasks
1–7), the same pattern data-store's `etf_holdings.weight_percent` widening used. The practical
consequence for `rebuild_returns`: it needs `mappings: TickerMappings` after all, to resolve each
`DIVIDEND` transaction's `instrument_identifier`/`kind` to the same `canonical_ticker` its
account's `BUY`/`SELL` rows resolved to — `position_snapshots.canonical_ticker` being pre-resolved
doesn't help here, since dividends are read from `transactions` directly. A `DIVIDEND` row that
doesn't resolve is silently excluded from `dividends_received_eur` rather than blocking the
ticker — the same sharp edge already documented for positions (an instrument split across a
mapped and an unmapped identifier kind for one broker), not a new one. `FEE`/`ADJUSTMENT` rows
still feed nothing anywhere in the service — an explicit, open gap, same status as the
already-flagged missing `realized_gains` table.

Tests: `tests/unit/datastore_client/test_http_client.py` (the three new methods, faking
`requests.get`, same convention `get_fx_rate`'s own tests use); `tests/unit/returns/
{test_calculator,test_repository}.py` (pure-function coverage including EUR/non-EUR conversion, a
price date preceding any FX point being skipped rather than zeroed, the zero-cost-basis → `None`
pct rule, dividend accumulation including a reopened-position case, and the true-upsert SQL
shape); `tests/unit/positions/test_repository.py` (the new `list_snapshots` read);
`tests/unit/test_pipeline.py` (`rebuild_returns` against fake repositories and a fake
`DataStoreClient` — ticker-existence blocking, an isolated failure per pair, the bounded window
for an open vs. closed position, `DIVIDEND` resolution via `mappings`, and the batched
dividend-transaction fetch); `tests/unit/{test_jobs,test_cli}.py` (the settings guard, run-result
shape, and CLI wiring, mirroring `rebuild-positions`'s own tests);
`tests/integration/test_rebuild_returns.py` (live Postgres, seeded through the real CLI stack —
the true-upsert contract a fake can't prove, a closed position's window stopping at the closing
date, an untracked ticker blocking with exit code 1, `--account` scoping, per-user isolation, and
a real `DIVIDEND` transaction flowing through to `dividends_received_eur`/`total_return_eur` —
with `jobs.HttpDataStoreClient` monkeypatched to a stub, matching task 14's own
`test_cashflow_import.py` precedent that the real HTTP path is `http_client.py`'s own unit tests'
job).

### 9. Portfolio HTTP API — done

New `src/portfolio/api/{app,routes,security,models}.py`. Routes are argument-parsing-plus-a-call
into `jobs.py`, `cli.py`'s own shape — accounts, manual/imported transactions, positions, returns.
`X-Internal-Secret` shared-secret check (`PORTFOLIO_INTERNAL_SECRET`) for `web`'s future
server-to-server calls, matching `auth`'s inbound convention rather than data-store's
`Authorization: Bearer` — deliberately a separate credential from the existing unprefixed
`INTERNAL_API_SECRET` (portfolio's own *outbound* credential to data-store), so the two rotate
independently. `create_app(settings: Settings | None = None)` is injectable, `auth`'s shape;
`/docs`/`/redoc`/`/openapi.json` disabled, `data-store`'s shape. `account_label`/`user_id` are
always a query parameter or a body field, never a path segment (labels are free text that can
contain `/`). Broker-file imports take a multipart `UploadFile` streamed to a size-capped temp
file, deleted in a `finally` on both the success and error path. `portfolio serve` new CLI
subcommand (`cli.py`, lazy-imports `uvicorn`/`fastapi`). New `Dockerfile`, `docker-compose.yml`
`db-init`/`api` services, `PORTFOLIO_INTERNAL_SECRET`/`PORTFOLIO_API_PORT` in `.env.example` and
`ci.yml`.

**Two preparatory changes, not in the original scope, both required before routes could exist
sensibly:**

`jobs.py` used to catch every `PortfolioError` and collapse it into a generic `{"status":
"partial", "rows_saved": 0}` result, the exception's message only logged — an HTTP layer reading
that dict alone couldn't distinguish "account not found" from "unparseable broker file" from
"data-store unreachable." `jobs.py` now lets `PortfolioError` propagate; `cli.py`'s `main()`
catches it alongside the pre-existing `SettingsError` clause; the API maps each subclass to its
own status code via app-level exception handlers (`AccountNotFound`→404,
`AccountLabelConflict`→409, four validation/parsing errors→422, `DataStoreApiError`→502,
everything else→500). `partial` now means only genuine partial success — the two rebuild flows'
own per-pair `blocked`/`failed_pairs` handling, unchanged.

`returns/repository.py` was write-only (`upsert_snapshots`, no reads at all), and
`positions/repository.py` could read only one `(account, ticker)` pair at a time — neither served
a "list my current positions" or "return history" request. Added `list_latest_snapshots`
(positions), `list_snapshots`/`list_latest_snapshots` (returns, its first reads), four pipeline
read flows (`list_transactions`/`list_positions`/`list_returns`/`list_latest_returns`, each
resolving `account_label` through the existing user-isolation chokepoint), and matching `jobs.py`
`get_*` functions returning plain dicts rather than a run result — mirroring data-store's own
`get_price_series` convention, since there's nothing to save and no `{"status", "rows_saved"}` to
report. Returns are served per ticker; aggregating across tickers into one portfolio-level series
is deliberately deferred to task 12, where the chart's actual shape will settle a real design
question (trading calendars differ per ticker) that the original scope didn't anticipate.

Tests: unit coverage for the two propagation-refactor changes (`tests/unit/test_jobs.py`,
`tests/unit/test_cli.py`), the new repository reads and pipeline flows
(`tests/unit/{positions,returns}/test_repository.py`, `tests/unit/test_pipeline.py`,
`tests/unit/test_jobs.py`), and the API itself under `tests/unit/api/` (security, app-level
exception mapping parametrized over every `PortfolioError` subclass, and route-level argument
coercion/status-code/upload-temp-file tests against a bare app with the secret dependency
overridden, `data-store`'s own `tests/unit/api/conftest.py` pattern). Integration:
`tests/integration/test_api.py`, a real `create_app()` against live Postgres — create-account-
then-list-it, an unknown account label returning 404 not 500, a second user's account label
staying invisible to the first, a missing secret returning 401 on every route but `/health`, and
add-transaction → rebuild-positions → read-positions end to end.

`services/portfolio/docs/architecture.md` updated throughout (new entry point per the
[architecture rule](../../.claude/rules/architecture.md)): the layering and deployment diagrams, the
flow table (an HTTP-endpoint column plus four new read-only rows), the configuration table, a new
"HTTP API" section (the error-mapping table, the account_label-never-a-path-segment decision, the
upload-to-temp-file contract, per-ticker returns, known limitations), and the testing/"where to
start reading" sections. No `schema.md` change — no DDL.

### 10. Web foundation & auth pages — done

FastAPI + Jinja2 scaffold, no build step (`src/web/{settings,cli}.py`, `api/{app,deps,routes,
csrf,security_headers}.py`, `auth_client/{base,http_client,errors}.py`). Base layout
(`templates/base.html`) mounts `brand/` as `StaticFiles` at `/brand` (a `WEB_BRAND_DIR` setting,
defaulted to the repo-root directory and overridden to a baked-in path in the Docker image) —
Fraunces headlines, Inter body, IBM Plex Mono for numbers, one `brand/tokens.css` on disk rather
than a copy. `GET`/`POST /login`, `/signup`, `/logout` call task 2's auth API server-to-server
through an `AuthClient` Protocol (mirroring `portfolio`'s `DataStoreClient` split) so routes are
testable against a fake with no live `auth`; the session cookie `auth` sets is proxied through
untouched, never rebuilt from a token in the response body and never exposed to browser JS. CSRF
via a stateless double-submit cookie (`api/csrf.py`) — no secret key or server-side store needed.
`web` forwards the browser's address to `auth` as `X-Forwarded-For` on every signup/login call —
`auth`'s login/signup throttles key on it, and without it every request looks like it came from
`web` itself (see `services/auth/docs/architecture.md`, "Login throttling and user enumeration") —
while `client_ip()` itself trusts only the raw socket address, never an inbound forwarded header,
since `web` is the public edge. htmx vendored into `static/vendor/htmx.min.js` (not a CDN) drives
form-submission feedback via an `HX-Redirect` response header on success and a swapped error
partial on failure; the same routes render a full page without it. `services/web/Dockerfile` and
`docker-compose.yml` mirror `auth`'s, minus the multi-service network wiring, which is task 13's.
Unit tests (`tests/unit/`: settings, CSRF, the `AuthClient` HTTP mapping via `httpx.MockTransport`,
routes against a fake `AuthClient`, and a mechanical check that `static/web.css` hardcodes no hex
colors) and an integration test (`tests/integration/test_auth_flow.py`: a real signup → home →
logout round trip through both services' actual code, `auth`'s ASGI app reached via a
`TestClient`-as-`httpx.Client` rather than a second container) under `services/web/tests/`.

### 11. Portfolio entry UI — done

New `src/web/portfolio_client/{base,http_client,errors}.py` — a copy of `auth_client`'s shape
(Protocol + `httpx`-backed implementation + one error hierarchy), per `web/docs/architecture.md`'s
own anticipation at task 9. `HttpPortfolioClient` dispatches on HTTP status code alone (every
`portfolio` domain error already has a distinct status code, unlike `auth`'s collapsed-401 cases),
and normalizes `portfolio`'s two 422 body shapes — a plain `{"detail": "<message>"}` and FastAPI's
own Pydantic-validation array — into one display string. Five new routes in `api/routes.py`: a
`GET`/`POST /accounts` hub (list + create-account form, broker field a text input with a
`<datalist>` seeded from `GET /brokers`), and two standalone forms reached via per-account links —
`/transactions/new?account=<label>` (manual entry) and `/transactions/import?account=<label>`
(broker-file upload, size-capped at 20MB client-side to match `portfolio`'s own cap before the
bytes are forwarded). `account_label` travels as a query param or hidden form field everywhere in
`web`, never a path segment, mirroring `portfolio`'s own API rule. A successful manual-transaction
add or import redirects back to the *same* form (`?saved=1` / `&imported=<rows_saved>`) rather than
to the hub, so entering several transactions for one account doesn't require re-navigating each
time. `InvalidPortfolioRequest`'s message is shown to the user verbatim (not mapped to canned copy
the way `auth_client`'s errors are) — there's no user-enumeration concern on a validation error like
there is on a login failure. New templates `accounts.html`/`transactions_new.html`/
`transactions_import.html`, extending `base.html` and reusing `partials/_form_errors.html`, plus a
"Portfolio" nav link and an updated `home.html` CTA. New settings `WEB_PORTFOLIO_API_URL`/
`WEB_PORTFOLIO_INTERNAL_SECRET` (a second, independently-rotatable secret from `WEB_INTERNAL_SECRET`,
mirroring `portfolio`'s own two-secret split); `app.py`'s lifespan now opens two `httpx.AsyncClient`s,
one per upstream service.

**One FastAPI/Starlette quirk found and worked around, not a design choice**: a required
`Form(...)` field treats a submitted empty string as *missing* rather than present-but-blank in the
pinned framework versions, which would 422 with a raw JSON body before a route's own CSRF check or
`portfolio`'s own blank-field validation ever ran. Every new route's string `Form` fields use
`Form(default="")` instead; `login_submit`/`signup_submit` (task 10) predate this finding and are
unchanged — see `docs/architecture.md`'s "Key decisions" for the revisit trigger.

Tests: `tests/unit/test_portfolio_client.py` (mirrors `test_auth_client.py`: one test per
status-code branch, both 422 body shapes, a connection error, a malformed 2xx body, the multipart
upload's request shape); `tests/unit/test_routes.py` gained a `FakePortfolioClient` (same
hand-written style as `FakeAuthClient`) and per-route coverage (CSRF rejection, success +
redirect/query-params, each mapped error, an unmapped-error default, one htmx variant);
`tests/unit/test_settings.py` gained the two new required-var cases. Integration:
`tests/integration/test_portfolio_flow.py`, a real `portfolio.api.app.create_app()` reached via
`httpx.ASGITransport` against `portfolio`'s own live Postgres — deliberately scoped to web↔portfolio
only (`require_user`/`optional_user` overridden to a fixed user rather than standing up a third live
service for a session round trip `test_auth_flow.py` already covers): create-account →
`/accounts` lists it; add-transaction → re-submitting the same `broker_transaction_ref` doesn't
duplicate the row; an unknown account surfaces 404; a broker file uploaded against a broker with no
registered parser surfaces `BrokerParserNotFound`'s message end-to-end (the happy-path upload isn't
covered here — parsing a real export is `portfolio`'s own tested territory, and this repo's dev venv
has no broker plugin installed per task 15).

### 12. Portfolio view UI — done

New `GET /portfolio?account=<label>` in `services/web/src/web/api/routes.py`: a per-account view
(`portfolio`'s own endpoints are all `account_label`-scoped, no cross-account aggregation exists,
so a cross-account rollup is out of scope here) listing current positions and a return-over-time
chart, read-only — it never triggers `rebuild-positions`/`rebuild-returns`, which stay CLI-only.
`portfolio_client/{base,http_client,errors}.py` (task 11) extended with `list_positions`/
`list_returns` (`Position`/`Return` dataclasses, field-for-field mirrors of `portfolio`'s
`PositionResponse`/`ReturnResponse`), parsing the wire's string-encoded `Decimal`/date fields; no
new error types needed. **Chart.js** chosen as the charting library (`web/docs/architecture.md`
had left this open), vendored into `static/vendor/chart.min.js` — the same no-CDN precedent htmx
already set, keeping `script-src 'self'`'s CSP untouched.

**The core new design problem, flagged by `portfolio/docs/architecture.md` as deferred here**:
`GET /returns` is per-ticker, not portfolio-aggregated, since different tickers can sit on
different trading calendars. New pure module `services/web/src/web/portfolio_view.py`
(`aggregate_portfolio_returns`) solves it: build the sorted union of every `price_date` across
every ticker, walk each ticker's series forward in lockstep with that union (forward-filling a
ticker's last-known row onto a date it lacks one of its own, contributing nothing to a date before
its first-ever row), sum the EUR figures across contributing tickers per date, and **recompute**
`total_return_pct` as `sum(total_return_eur)/sum(cost_basis_eur)` rather than averaging the
per-ticker percentages (which don't sum to a portfolio percentage). A companion
`filter_open_positions` drops `quantity == 0` rows from the positions table (`portfolio`'s
`list_latest_snapshots` intentionally doesn't prune closed positions) but keeps a negative
quantity visible — a replay anomaly `positions/calculator.py` warns about but still computes, not
a modeled short position, and worth surfacing rather than hiding.

**A CSP wrinkle, resolved without weakening the CSP**: canvas-based Chart.js can't read
`var(--forest)` the way SVG can, but hardcoding a hex value would violate the brand rule and (for
`.html`) its mechanical hex-scan test; `web`'s CSP (`script-src 'self'`, no `unsafe-inline`, no
nonce) also rules out an inline `<script>` with real logic — this codebase had never had one.
Resolved by embedding the chart's numeric data as a non-executable `<script
type="application/json">` data island in the new `templates/portfolio.html`, with the actual logic
(`getComputedStyle(...)` reading `--forest`/`--oxblood`/`--hairline` at runtime, the `Chart`
instantiation) in a same-origin `static/portfolio_chart.js`, loaded exactly like vendored htmx — no
CSP change anywhere. New `tests/unit/test_templates.py::test_templates_have_no_inline_executable_script`
makes that invariant mechanical. The chart uses a `category` x-axis rather than Chart.js's `time`
scale, avoiding a second vendored date-adapter dependency at the cost of a calendar gap rendering
as an equal-width step rather than a proportionally wider one — an accepted v1 simplification.

`GET /portfolio` treats `AccountNotFound` (from either the positions or returns call) as fatal — a
distinct 404 — rather than folding it into the generic degrade-to-inline-error path task 11's
`_list_accounts_and_brokers` established, since a bad `account_label` has nothing partial to show
either way; any other `PortfolioClientError` on either call degrades that half of the page
independently, mirroring that same established pattern. An account with no positions and/or no
return history yet renders as an ordinary empty state, never an error — `web` has no way to tell
"never rebuilt" from "rebuilt into zero rows" and doesn't need to. One new link on
`accounts.html` ("View portfolio") reaches the new page per account.

Tests: `tests/unit/test_portfolio_view.py` (new — the aggregation function's edge cases: empty
input, staggered trading calendars, forward-fill after a ticker's first row but never before it, a
fully-closed ticker's zeroed row forward-filling correctly, `total_return_pct` recomputed from
summed amounts rather than averaged, `None` when summed cost basis is 0, out-of-order input);
`tests/unit/test_portfolio_client.py` (the two new methods: happy path, `AccountNotFound`,
malformed body); `tests/unit/test_routes.py` (`FakePortfolioClient` extended, the new route's
login-required/happy-path/closed-position-filtering/empty-state/404/degrade-on-partial-failure
cases, the new accounts-page link); `tests/unit/test_templates.py` (the new no-inline-script
check). Integration: `tests/integration/test_portfolio_flow.py` gained an empty-state-before-any-
rebuild case, an unknown-account-404 case, and a positions-populated case (seeded through the real
web→portfolio round trip, `portfolio.jobs.run_rebuild_positions` called directly — needs no live
`data-store`, unlike returns rebuild); a returns-populated case is explicitly out of scope there,
noted in the file's own docstring, since `rebuild-returns` needs a live `data-store` this test
harness doesn't have.

### 13. End-to-end integration & smoke test — done

Each service kept its own independent `docker-compose.yml`, joined by a new external Docker
network, `stakehold-net`, created once per machine (`docker network create stakehold-net`) and
attached only via a new opt-in overlay per service, `docker-compose.stakehold-net.yml`, passed
alongside the base file with a second `-f`. A single merged root compose file (via `include:`)
was tried first and rejected: `auth`, `portfolio` and `data-store`'s compose files all name their
API service `api` — merging all four into one Compose project silently collapses three services
into one, since Compose merges by service key, not by file (verified empirically before
committing to the design). The overlay approach keeps every service's own compose file
byte-for-byte usable standalone exactly as before. Each overlay joins only the service actually
called cross-stack (`api`/`web`, never `postgres`/`db-init`/`pgadmin`/the Airflow containers) and
overrides the caller-side settings that need an in-network address instead of
`localhost:<published-port>` — `WEB_AUTH_API_URL`/`WEB_PORTFOLIO_API_URL` (web's overlay) and
`DATASTORE_API_URL` (portfolio's overlay, pointed at `data-store-api`'s *container* port `8000`,
not its host-published `8082`). `app-postgres`'s compose file stays inside `services/portfolio/`
— task 13 put every service's *API* on the shared network but added no second database tenant, so
the "second tenant to justify the move" condition `docs/architecture.md` had flagged still hasn't
arrived; that passage is rewritten as a considered no rather than left stale. New root
`docs/smoke-test.md` (the full bring-up/verify/manual-flow/teardown sequence) plus a short
"Running the full stack" section in `README.md` pointing at it. `.env.example` in `auth`,
`portfolio`, `data-store` gained the missing reciprocal comment naming each shared secret's
counterpart var+file (`web`'s already had them); `data-store/.env.example` also had a small
inaccuracy fixed in passing — its `INTERNAL_API_SECRET` comment claimed `web` was a caller
alongside `portfolio`, but `web` never calls `data-store` directly. `docs/architecture.md` updated
in all four services per the architecture rule: `web`'s and `auth`'s deployment sections describe
the actual mechanism instead of pointing at "task 13" as future work; `auth`'s "service-to-service
trust" note now states plainly that `stakehold-net` is an ordinary bridge with no inter-container
firewalling — the shared secret, not network segmentation, is what actually separates a
legitimate caller; `data-store`'s deployment diagram drops the stale `"(future)"` label on
`portfolio` as a caller.

**The actual smoke test surfaced two real, pre-existing problems that per-service testing had no
way to catch** — arguably the whole point of this task existing separately from each service's
own suite:

1. **All four services' locally-built Docker images were stale relative to current source** —
   `portfolio`'s by up to 18 days (missing everything from task 9 onward, judging by `git log` on
   `services/portfolio/src`), `web`'s missing tasks 11 and 12 entirely (`/accounts`,
   `/transactions/*`, `/portfolio` all 404'd until rebuilt). Nothing rebuilds these images
   automatically; `docker compose up -d` happily reuses whatever was last built. Rebuilt all four
   (`docker compose ... build`) before the smoke test could mean anything.
2. **`auth-postgres`'s persisted role password had drifted from `services/auth/.env`.** Attaching
   `auth-api` to `stakehold-net` forced Compose to recreate `auth-postgres` (likely a `postgres:17`
   base-image update — `portfolio`'s and `data-store`'s postgres containers didn't need
   recreating), which reused the existing data directory untouched (no data lost) but exposed that
   the role's real password no longer matched `.env`'s current value — the previous containers had
   simply been running for 7 hours on a cached value from before whatever edit caused the drift.
   Fixed by resetting the role's password to `.env`'s current value from inside the container
   (`ALTER ROLE ... WITH PASSWORD`, value never printed), per the user's direction — not something
   fixed unilaterally, since it's a live-credential change.

A local `services/portfolio/configs/ticker_mappings.yaml` (gitignored, not committed — see
`ticker_mappings.yaml.example`) was created to give the smoke test a real, mapped instrument to
rebuild against (one ISIN → `CAT`, a ticker `data-store` already had computed returns for);
without it every transaction would be reported `blocked` as unmapped, which is correct behavior
but wouldn't exercise the returns/positions rendering path.

**Verified end to end** against the real, networked stack: signed up, logged in, created an
account, entered a manual `BUY` transaction, ran `rebuild-positions` and `rebuild-returns` via
`docker compose exec` (the latter calling `data-store` over `stakehold-net`, confirming the
`portfolio`↔`data-store` `INTERNAL_API_SECRET` pair already matched in the existing `.env` files),
and confirmed `GET /portfolio?account=...` rendered the position and an 11-point return series.
Broker-file import wasn't separately exercised — manual entry and broker import share the same
`add_transaction`/import pipeline downstream of the form, and task 6's own tests already cover the
parser path with no plugin installed in this dev environment (task 15).

### 14. Broker cash-ledger import — done

Surfaced by task 6, not originally scoped: one broker's parser handles that broker's trade export
only — deposits, withdrawals, dividends, and dividend tax live in that broker's separate "Account"
cash-ledger export. Without this, that broker's cash balance and dividend history were permanently
incomplete regardless of how many times the trade export was re-imported.

The cash-ledger parser is deliberately **not** a `BrokerParser` — its rows carry no EUR-equivalent
at all (unlike the trade export), so a non-EUR row needs a live FX-rate lookup mid-parse; widening
the `BrokerParser` Protocol for every parser to carry a dependency only this one needs was rejected
in favor of a dedicated flow (task 15 later generalized this into the parallel
`CashflowParser`/`CASHFLOW_PARSERS` extension point `docs/architecture.md` now documents; at this
task it was still one hardcoded, broker-specific CLI command).

**Scope grew mid-task, revising the original estimate**: resolving those FX rates meant building
`datastore_client/base.py` (the `DataStoreClient` Protocol) and `http_client.py` (`get_fx_rate`
only) now, pulled forward from task 8 rather than deferred — see task 8's updated scope, which
inherits this rather than building it from scratch. New `errors.py`: `DataStoreApiError`. New
settings: `DATASTORE_API_URL`, `INTERNAL_API_SECRET` (unprefixed, matching data-store's own name —
the one shared credential both sides must agree on), both optional except when a cash-ledger
import runs. `requests` added as a portfolio dependency (data-store's own existing choice for
outbound HTTP).

Row classification against a real, ~900-row, non-English-locale account statement — which
description strings map to which canonical type, which rows are the broker's own internal
housekeeping to skip, the exact real-export verification counts — is written up in the private
plugin package's own docs as of task 15, not here; `docs/architecture.md`'s "Cash-ledger imports
needing a live FX lookup" section covers the FX-resolution contract every such parser follows,
generically.

Tests (moved to the private plugin package at task 15, except the last):
fixture-trimmed-from-a-real-export parser test with a plain fake `resolve_fx_rate` callable;
`tests/unit/datastore_client/test_http_client.py` (a faked `requests.get`, no live data-store
instance — matching the documented "that boundary is the API contract itself" policy, stayed
here — genuinely broker-agnostic); `tests/integration/test_cashflow_import.py` (idempotent
reimport, broker-mismatch rejection; routed through `pipeline.py` directly with a stub
`DataStoreClient`, not through `jobs.py`'s real HTTP client — stayed here too, rewritten at task
15 to exercise a synthetic parser instead of a real one).

### 15. Extract broker-parser plugins to a private repo — done

Stakehold is a private repo today but is meant to go public eventually as a portfolio piece.
Tasks 6 and 14's broker-specific parsing code and real, trimmed export fixtures reveal exactly
which two brokers the author personally uses — a narrower, more useful phishing/account-recovery
target than "runs a portfolio app." Moves that code to a separate private repo
(`stakehold-brokers`), while keeping the general extension-point architecture public.

**Wiring, one direction only, no framework dependency added to this repo**: `stakehold-brokers`
depends on `portfolio` (a git dependency pinned to a commit SHA) for `CanonicalTransaction`,
`BrokerParser`/`CashflowParser`, and the relevant `errors.*` classes; it registers its parser
classes under the `stakehold.broker_parsers`/`stakehold.cashflow_parsers` Python entry-point
groups in its own `pyproject.toml`. `portfolio` never imports or depends on `stakehold-brokers` —
`pipeline.py` discovers whatever is installed via `importlib.metadata.entry_points()`
(`broker_import/discovery.py`, new). This is a plugin architecture, not a dependency: this repo's
CI never needs a credential to a private repo, and a public contributor's `uv sync` is never
broken by a private dependency they lack access to. The plugin package is a local add-on for the
author's own deployment (`uv pip install --no-deps -e ../stakehold-brokers`, outside
`uv sync`/`uv.lock`).

**Real bug caught before the extraction, fixed first as its own change**: `add_account` used to
gate account creation on `broker in BROKER_PARSERS` — harmless while that dict was hardcoded and
never empty, but fatal once it became entry-point-discovered: a deployment with zero broker
plugins installed would reject *every* account, and since manual transaction entry requires an
existing account, that silently broke manual entry too, for a flow with no real dependency on any
parser. Fixed by making `broker` a free-text label at account-creation time (`accounts.broker` was
always an unconstrained column at the DB layer, per `schema.md`), validated only for non-blank
(new `InvalidBroker`); `BROKER_PARSERS`/`CASHFLOW_PARSERS` are read only where a parser is
actually needed — `import_transactions`/`import_cashflow`, both raising a renamed
`BrokerParserNotFound` that lists what's currently registered. New `portfolio broker list`
(`pipeline.list_available_brokers`) for discoverability.

**The Acme-only cash-flow flow (task 14) needed real generalization, not just a file move**:
`import_acme_cashflows`/`run_import_acme_cashflows`/the `import-acme-cashflows` CLI command
hardcoded one broker's name throughout the orchestration layers, not just in
`broker_import/`. Renamed to `import_cashflow`/`run_import_cashflow`/`import-cashflow --broker
<key>`, backed by a new `CashflowParser` Protocol + `CASHFLOW_PARSERS` registry (`broker_key`
carrying `resolve_fx_rate` explicitly, mirroring `BrokerParser`/`BROKER_PARSERS` but for formats
needing a live FX lookup mid-parse) — a real second extension point, not a rename.

**Sequenced in four stages, each independently green**: (1) build entry-point discovery and the
`CashflowParser` generalization while both parsers still self-register from inside this package
(`pyproject.toml`'s own `[project.entry-points]` table, removed once step 3 completes) — proves
discovery end-to-end before anything moves; (2) stand up `stakehold-brokers`'s package layout and
`pyproject.toml`, verified with its own standalone `uv sync && pytest` — the actual go/no-go gate
on whether a git+`subdirectory` dependency on `portfolio` resolves for an external, non-workspace
consumer; (3) install it into this workspace's shared venv (`uv pip install --no-deps -e`,
`--no-deps` to avoid clobbering the live-editable `portfolio`/`shared` workspace members),
confirm `pipeline.BROKER_PARSERS`/`CASHFLOW_PARSERS` resolve to the new package rather than the
still-present-but-now-unregistered local files, *then* delete `broker_import/{acme,globex,
acme_cashflow}.py` and their real-fixture-backed unit tests from this repo, rewriting
`tests/integration/{test_broker_import,test_cashflow_import}.py` to use a synthetic
`BrokerParser`/`CashflowParser` instead — they were really testing
`transactions/repository.py`'s idempotent-insert and multi-user-isolation semantics, not
broker-specific parsing, so this suite no longer depends on any plugin being installed; (4)
redact `docs/architecture.md` (the "What each parser actually does" section — real Order-ID-
collision counts, the full non-English cash-ledger classification table — was the single most
information-dense broker-identifying content in the whole repo, more so than the code itself;
moved verbatim into `stakehold-brokers`'s own new `docs/architecture.md`) and the committed
`configs/ticker_mappings.yaml.example` (dropped a literal broker name used as an example section
key).

**Git history not preserved for the moved files, deliberately**: each had exactly one commit in
`git log --follow` (squashed feature-branch merges), nothing meaningful to carry over, and
`git-filter-repo` wasn't installed — a fresh copy plus a plain delete commit here was the
straightforward call, not a hard one.

**Verified the original motivation holds end-to-end, not just asserted**: with
`stakehold-brokers` uninstalled, `portfolio broker list` reports zero and the full test suite
(810 unit, 15 integration) still passes — a deployment that downloads only this repo can still
create accounts and record transactions manually; only broker-file import needs the plugin.
