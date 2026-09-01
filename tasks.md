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
| 7 | [Positions](#7-positions) | 6 | ready |
| 8 | [Returns](#8-returns) | 7, 3 | blocked |
| 9 | [Portfolio HTTP API](#9-portfolio-http-api) | 5, 6, 7, 8 | blocked |
| 10 | [Web foundation & auth pages](#10-web-foundation--auth-pages) | 2 | ready |
| 11 | [Portfolio entry UI](#11-portfolio-entry-ui) | 10, 9 | blocked |
| 12 | [Portfolio view UI](#12-portfolio-view-ui) | 11 | blocked |
| 13 | [End-to-end integration & smoke test](#13-end-to-end-integration--smoke-test) | 12 | blocked |
| 14 | [Acme cash-flow import](#14-acme-cash-flow-import) | 6 | done |

Two tasks are `ready` today: **7** (positions) and **10** (web) — independent of each other, both
unblocked now that task 6 is `done`.

## Tasks

### 1. Auth schema & foundation — done

`services/auth/docs/schema.md` written and signed off per
[`data-schema.md`](.claude/rules/data-schema.md): `users` (with `display_name`) and `sessions`
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
[`data-schema.md`](.claude/rules/data-schema.md) and implemented as `src/portfolio/db/schema.sql`
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
portfolio dependency). `broker_import/base.py` (the `BrokerParser` Protocol) + `acme.py` +
`globex.py`, both stdlib `csv`/`hashlib`/`zoneinfo` — no pandas. `pipeline.py`'s `BROKER_PARSERS`
registry replaces task 5's placeholder `KNOWN_BROKERS` tuple. Flows: `import-transactions --user
<id> --broker <key> --file <path> --account <label>`, `list-unmapped-instruments --user <id>`.
New `errors.py` subclasses: `BrokerMismatch`, `BrokerFileError`, `TickerMappingError`. New
`transactions/repository.py` read: `list_distinct_instruments`, joined to `accounts` for both
`user_id` isolation and the owning broker (SYMBOL mappings are broker-scoped).

**Three corrections to `docs/architecture.md`, made while parsing real ACME and Globex export
files (`dev/porfolio/`, gitignored) rather than assumed from the documented formats.** Globex's
`Gross Amount`/`Net Amount` are already EUR-denominated (confirmed by recovering Globex's own $1
minimum commission from a USD trade converted back at its derived rate) — the two FX design risks
`architecture.md` had flagged for `globex.py` (companion-row matching, a data-store FX-series
fallback) don't apply; neither `datastore_client` nor a live data-store instance is a dependency of
this task. ACME's `Order ID` is not row-unique — a real export contains a partial fill split
across two rows sharing one Order ID — so `broker_transaction_ref` is `<order_id>:<content
hash>:<occurrence index>`, not the Order ID alone (verified: 82 real rows in, 82 distinct refs
out; Globex's real file: 68 in, 68 out). `import-transactions --account <id>` in the original flow
table couldn't be built as documented — `accounts/repository.py` deliberately has no unscoped
`get_by_id` — so it takes `--account <label>`, resolved via `get_by_label` like manual entry.

**One scope gap surfaced, not silently absorbed**: ACME's `Transactions.csv` (what `acme.py`
parses) carries trades only — deposits, withdrawals, and dividends live in ACME's separate
`Account.csv` export, unparsed by this task. See task 14. Also unverified for lack of a real
example: `Sell`, `Withdrawal`, and `Dividend` rows in the Globex format, including whether Globex signs
a Sell's quantity negative — flagged in `broker_import/globex.py`'s own docstring, not assumed.

Tests: `tests/unit/broker_import/` against fixture files trimmed from the real exports, including
the Globex and ACME content-hash dedup logic and ACME's shared-Order-ID partial-fill case;
`tests/unit/ticker_mapping/`; `tests/integration/test_broker_import.py` (idempotent re-import,
`list_distinct_instruments` multi-user isolation) against live Postgres.

### 7. Positions

`positions/calculator.py` (pure function replaying the transaction log) + `repository.py`
(delete-then-insert per `(account_id, canonical_ticker)`, each pair's replacement atomic). Flow:
`rebuild-positions`. An unmapped instrument blocks its own rebuild with a `partial` status, never a
silent skip. Tests.

### 8. Returns

`datastore_client/base.py` (the `DataStoreClient` Protocol) and `http_client.py` (the real
implementation, calling task 3's API) already exist as of task 14, pulled forward because Acme's
cash-ledger import needed an FX lookup — but only `get_fx_rate` (a single-date lookup) is built.
This task extends `http_client.py` with the price-series and ticker-existence methods (`GET
/prices/{ticker}`, `GET /tickers/{ticker}`) and, if a whole-range FX signature turns out cleaner
for `returns/calculator.py`'s needs than repeated single-date `get_fx_rate` calls, widens that too
— not a from-scratch build. `returns/calculator.py` (pure, takes prices/FX as plain data) +
`repository.py` (true upsert, matching `market_returns`'s grid shape). Flow: `rebuild-returns`.
Tests (a stub `DataStoreClient` covers everything except the real HTTP client against a live
task-3 endpoint, which `datastore_client/http_client.py`'s own unit tests — faking `requests.get`,
not a live instance — already cover for `get_fx_rate`).

### 9. Portfolio HTTP API

Not yet designed anywhere — `portfolio/docs/architecture.md` only shows it as a future box in the
layering diagram. FastAPI routes that are argument-parsing-plus-a-call into `jobs.py`, same as
`cli.py` — accounts, transactions (manual entry + import), positions, returns. Shared-secret check
for `web`'s server-to-server calls. Update `services/portfolio/docs/architecture.md` (new entry
point per the [architecture rule](.claude/rules/architecture.md)).

### 10. Web foundation & auth pages

FastAPI + Jinja2 + htmx scaffold, no build step. Base layout using the brand tokens
(`brand/tokens.css`) — Fraunces headlines, Inter body, IBM Plex Mono for numbers. Register, login,
logout pages calling task 2's auth API server-to-server; session identity forwarded as a cookie,
never exposed to browser JS. CSRF protection on state-changing routes (web's own responsibility,
per its architecture doc). **Must forward the browser's address to `auth` as `X-Forwarded-For`** —
`auth`'s login/signup throttles key on it, and without it every request looks like it came from
`web` itself (see `services/auth/docs/architecture.md`, "Login throttling and user enumeration").

### 11. Portfolio entry UI

Pages/forms calling task 9's portfolio API: create an account, manually enter a transaction,
upload a broker export file. htmx partial updates for form submission feedback.

### 12. Portfolio view UI

Views of the portfolio by account and by value/returns: position listing per account, return-over-
time chart (a lightweight JS charting library, per `web/docs/architecture.md` — not yet named,
needs picking as part of this task), calling task 9's positions/returns endpoints. Gains/losses
rendered with `--forest`/`--oxblood` per the brand charter, numbers in `--font-mono`.

### 13. End-to-end integration & smoke test

Full deployment wiring: all four services, three Postgres instances (`auth-postgres`,
`app-postgres`, `data-store-postgres`), shared internal secrets between services. Manual smoke
test of the whole flow: register → login → add account → enter/import transactions → rebuild
positions/returns → view in the web UI.

### 14. Acme cash-flow import — done

Surfaced by task 6, not originally scoped: `broker_import/acme.py` parses ACME's
`Transactions.csv`, which carries trades only — deposits, withdrawals, dividends, and dividend tax
live in ACME's separate "Account" cash-ledger export. Without this, a ACME account's cash
balance and dividend history were permanently incomplete regardless of how many times
`Transactions.csv` was re-imported.

`broker_import/acme_cashflow.py` — deliberately **not** a `BrokerParser`, not registered in
`BROKER_PARSERS`. This file format's rows carry no EUR-equivalent at all (unlike either existing
export), so a non-EUR row needs a live FX-rate lookup mid-parse; widening the `BrokerParser`
Protocol for every parser to carry a dependency only this one needs was rejected in favor of a
dedicated flow — `pipeline.import_acme_cashflows`, its own CLI command
(`import-acme-cashflows --user <id> --file <path> --account <label>`), not a branch inside
`import-transactions`.

**Scope grew mid-task, revising the original estimate**: resolving those FX rates meant building
`datastore_client/base.py` (the `DataStoreClient` Protocol) and `http_client.py` (`get_fx_rate`
only) now, pulled forward from task 8 rather than deferred — see task 8's updated scope, which
inherits this rather than building it from scratch. New `errors.py`: `DataStoreApiError`. New
settings: `DATASTORE_API_URL`, `INTERNAL_API_SECRET` (unprefixed, matching data-store's own name —
the one shared credential both sides must agree on), both optional except when
`import-acme-cashflows` runs. `requests` added as a portfolio dependency (data-store's own
existing choice for outbound HTTP).

Classification against a real, ~900-row Polish-locale account statement: every row carrying an
Order Id duplicates a trade/fee already imported via `acme.py` and is skipped; internal ACME
cash/currency housekeeping (sweeps, FX conversion legs, its money-market fund) is skipped too, not
imported as noise; the remainder maps into DEPOSIT/WITHDRAWAL/DIVIDEND/FEE/ADJUSTMENT — see
`docs/architecture.md`'s "The Acme cash-ledger import" section for the full table and the
locale caveat (an export in a different display language raises `BrokerFileError` on its first
unrecognized string rather than guessing). Verified against the full real export: 339 real cash
events kept out of 889 raw rows, 173 needing a resolved FX rate, none unresolvable, no ref
collisions.

Tests: `tests/unit/broker_import/test_acme_cashflow.py` (fixture trimmed from the real export,
a plain fake `resolve_fx_rate` callable); `tests/unit/datastore_client/test_http_client.py` (a
faked `requests.get`, no live data-store instance — matching the documented "that boundary is the
API contract itself" policy); `tests/integration/test_acme_cashflow_import.py` (idempotent
reimport, broker-mismatch rejection; routed through `pipeline.py` directly with a stub
`DataStoreClient`, not through `jobs.py`'s real HTTP client).
