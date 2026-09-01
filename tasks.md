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
| 6 | [Broker import](#6-broker-import) | 5 | ready |
| 7 | [Positions](#7-positions) | 6 | blocked |
| 8 | [Returns](#8-returns) | 7, 3 | blocked |
| 9 | [Portfolio HTTP API](#9-portfolio-http-api) | 5, 6, 7, 8 | blocked |
| 10 | [Web foundation & auth pages](#10-web-foundation--auth-pages) | 2 | done |
| 11 | [Portfolio entry UI](#11-portfolio-entry-ui) | 10, 9 | blocked |
| 12 | [Portfolio view UI](#12-portfolio-view-ui) | 11 | blocked |
| 13 | [End-to-end integration & smoke test](#13-end-to-end-integration--smoke-test) | 12 | blocked |

One task is `ready` today: **6** (broker import), unblocked since task 5 is `done`. **11** stays
`blocked` — it depends on both 10 (now `done`) and 9 (portfolio HTTP API, still `blocked`).

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
