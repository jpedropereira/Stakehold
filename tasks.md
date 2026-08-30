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
| 2 | [Auth identity API](#2-auth-identity-api) | 1 | ready |
| 3 | [Data-store read API](#3-data-store-read-api) | — | done |
| 4 | [Portfolio foundation & schema sign-off](#4-portfolio-foundation--schema-sign-off) | — | ready |
| 5 | [Accounts & manual transactions](#5-accounts--manual-transactions) | 4 | blocked |
| 6 | [Broker import](#6-broker-import) | 5 | blocked |
| 7 | [Positions](#7-positions) | 6 | blocked |
| 8 | [Returns](#8-returns) | 7, 3 | blocked |
| 9 | [Portfolio HTTP API](#9-portfolio-http-api) | 5, 6, 7, 8 | blocked |
| 10 | [Web foundation & auth pages](#10-web-foundation--auth-pages) | 2 | blocked |
| 11 | [Portfolio entry UI](#11-portfolio-entry-ui) | 10, 9 | blocked |
| 12 | [Portfolio view UI](#12-portfolio-view-ui) | 11 | blocked |
| 13 | [End-to-end integration & smoke test](#13-end-to-end-integration--smoke-test) | 12 | blocked |

Two tasks are `ready` in parallel today: **2** (auth) and **4** (portfolio) — neither depends on
the other, and **1** and **3** are now `done`.

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

### 2. Auth identity API

FastAPI app exposing the expected endpoint surface: signup, login, logout, session validation.
Argon2id password hashing (`argon2-cffi`). Opaque session cookies (`httpOnly`, `Secure`,
`SameSite=Lax`), sliding renewal on validation. Shared-secret check for internal server-to-server
callers. Unit + integration tests (`tests/integration/` needs live Postgres).

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

Left out of scope, noted for whoever picks up task 13: `Dockerfile.airflow` has the same
`uv sync` workspace-member gap `Dockerfile.api` hit and fixed (it `COPY`s only `data-store` and
`portfolio`, predating `auth`/`web`) — untouched here since fixing another image isn't this task's
job, but it will fail the next time someone actually rebuilds it.

### 4. Portfolio foundation & schema sign-off

`services/portfolio/docs/schema.md` is already drafted in detail but is explicitly marked
"proposal, not yet implemented" — confirm it (or amend) with the user before writing
`schema.sql`, per [`data-schema.md`](.claude/rules/data-schema.md). Then: `settings.py`,
`db/connection.py` + `schema.sql`, `jobs.py`/`pipeline.py` skeleton, `run_result.py` (same
`{"status", "rows_saved"}` contract as data-store's), `cli.py` skeleton, `app-postgres` wiring.

### 5. Accounts & manual transactions

`accounts/repository.py` (the multi-user isolation chokepoint — every other module's queries pass
through it), `transactions/base.py` (the `CanonicalTransaction` shape) + `repository.py`
(idempotent insert, `ON CONFLICT ... DO NOTHING`). CLI flows: `account add`, `add-transaction`.
Tests (unit: pure logic; integration: idempotent-import semantics against live Postgres).

### 6. Broker import

`ticker_mapping/loader.py` (reads/validates `configs/ticker_mappings.yaml`). `broker_import/
base.py` (the `BrokerParser` Protocol) + `acme.py` + `globex.py`. Flows: `import-transactions
--broker <key> --file <path> --account <id>`, `list-unmapped-instruments`. Tests against fixture
files for both parsers, including the Globex content-hash dedup logic.

### 7. Positions

`positions/calculator.py` (pure function replaying the transaction log) + `repository.py`
(delete-then-insert per `(account_id, canonical_ticker)`, each pair's replacement atomic). Flow:
`rebuild-positions`. An unmapped instrument blocks its own rebuild with a `partial` status, never a
silent skip. Tests.

### 8. Returns

`datastore_client/base.py` (the `DataStoreClient` Protocol) + `http_client.py` (the real
implementation, calling task 3's API). `returns/calculator.py` (pure, takes prices/FX as plain
data) + `repository.py` (true upsert, matching `market_returns`'s grid shape). Flow:
`rebuild-returns`. Tests (a stub `DataStoreClient` covers everything except the real HTTP client
against a live task-3 endpoint).

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
per its architecture doc).

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
