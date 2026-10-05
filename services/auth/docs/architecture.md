# auth architecture

**Status: schema/foundation (task 1) and the identity HTTP API (task 2) both implemented.**

## Responsibility

`auth` owns credentials, sessions, login, and the `users` record itself — from the start, not as a
handoff target. No other service keeps its own copy of a user's identifying details (email, display
name); every other service, starting with [`portfolio`](../../portfolio/docs/architecture.md),
references a user only by the UUID `auth` issues at signup, as a trusted plain column with no local
`users` table of its own.

## Stack

- **FastAPI**, with its own dedicated Postgres — deliberately isolated from `app-postgres` given
  the sensitivity of what it stores (password hashes, session tokens). See
  [`portfolio`'s deployment section](../../portfolio/docs/architecture.md#deployment) for the full
  reasoning and how this reconciles with `app-postgres` being the shared database every other
  future non-data-store service uses instead of standing up its own instance.
- **Argon2id** via `argon2-cffi` for password hashing, not bcrypt.
- **Sync route handlers, not `async def`.** psycopg is used synchronously here (a
  `psycopg_pool.ConnectionPool`, not the async pool variant), so routes are plain `def`s — FastAPI
  runs those in its threadpool, which keeps a blocking DB call from stalling the event loop without
  needing an async driver. `data-store` and `portfolio` (tasks 3, 9) are expected to copy this
  shape rather than half-adopt async: mixing a sync DB layer with `async def` routes would silently
  block the loop instead.
- **`psycopg_pool.ConnectionPool`**, opened in the FastAPI lifespan and closed on shutdown (via
  [`shared.db.pool.connection_pool`](../../shared/docs/architecture.md)), one connection checked
  out per request. Chosen over opening a fresh connection per request (`shared.db.connection.
  get_connection`, which one-shot CLI/job entry points still use) because `/sessions/validate` is
  called on every page `web` renders — paying a full TCP-plus-auth handshake on each of those would
  be the dominant cost of the whole request.

## Key decisions

- **Stayed in Python/FastAPI rather than an off-the-shelf identity provider** (Keycloak,
  Authentik, Ory). Those are reasonable once there's a real need for MFA or social login, but
  excess complexity for a solo hobby project with no users yet.
- **Owns the only `users` table in the application**: `users(id UUID, email, display_name,
  password_hash, created_at)` — see [`docs/schema.md`](schema.md) for the column-level DDL,
  signed off per [`data-schema.md`](../../../.claude/rules/data-schema.md). `id` is a UUID v7
  generated at signup,
  chosen over v4 for index locality and sortable creation order — no meaningful downside for a
  non-secret identifier (unlike a session token, where randomness is what matters). Every other
  service that references a user — starting with `portfolio.accounts.user_id` — stores that UUID as
  a trusted plain column, never a local copy of `email`/`display_name` and never a cross-database
  foreign key, since services only communicate through APIs. **This is the design from day one, not
  a migration target**: `portfolio` never has its own `users` table to hand off, which avoids the
  ID-remapping migration a local placeholder table would have eventually forced.
- **Session cookies, not JWTs** — specifically an opaque session token (a random session ID), not
  a signed/stateless token. `web` is server-rendered and same-origin, so no token needs to reach
  the browser except the opaque cookie value; opaque tokens make logout/revocation trivial
  (delete the row) compared to signed tokens, which need a deny-list.
- **Session validation is a per-request API call** (e.g. `/sessions/validate`) from `web`,
  deliberately not optimized with caching yet — acceptable at hobby-project request volumes,
  revisit only if it becomes a real bottleneck.
- **Session storage**: a `sessions` table in `auth`'s own Postgres — see
  [`docs/schema.md`](schema.md) for the column-level DDL. The cookie carries a random opaque
  token; the table stores only its `SHA-256` hash (`token_hash`), not the token itself, so reading
  the table can't be replayed as a live session. Sliding expiry is on the order of 7–14 days,
  renewed on validation, alongside a fixed `absolute_expires_at` cap set at login that renewal
  never extends — see "Open questions" below for why.
- **Cookie flags**: `httpOnly`, `Secure`, `SameSite=Lax`.
- **Service-to-service trust, resolved for now**: `auth` and `portfolio` are not reachable from
  outside the deployment's private network — only `web` is publicly exposed — and every internal
  call additionally carries a shared secret (an env-var-configured bearer token) that the receiving
  service checks before processing the request. This proves "a legitimate internal caller," not
  cryptographically that a specific `user_id` came from `auth` — an accepted gap at this stage, see
  the signed-assertion upgrade path under "Open questions."

## Endpoint surface

`auth.api` (`src/auth/api/{app,deps,cookies,schemas,routes}.py`). Every route below except
`/health` requires the shared internal secret (`X-Internal-Secret` header — see "Service-to-service
trust" below); failure responses are deliberately uniform, so a bad password, an unknown email, and
an expired session are indistinguishable to the caller.

| Endpoint | Success | Notes |
|---|---|---|
| `POST /signup` | `201` + `Set-Cookie` | body `{user_id, email, display_name}`; `409` on a duplicate email; `429` once the signup throttle trips |
| `POST /login` | `200` + `Set-Cookie` | `401` on wrong password or unknown email (identical response either way — see "user enumeration" below); `429` once the login throttle trips |
| `POST /sessions/validate` | `200` + refreshed `Set-Cookie` | reads the `Cookie` header; `401` on missing/expired/unknown session |
| `POST /logout` | `204` + cookie cleared | idempotent — `204` even for a missing or unrecognized token |
| `GET /health` | `200` | unauthenticated; backs the container healthcheck. `503` if the database is unreachable **or** the schema hasn't been applied — a liveness-only check would report ready before `auth db init` has run, while every other route 500s on `relation "users" does not exist` |

`auth` sets the `Set-Cookie` header itself (name `stakehold_session`, flags below) — `web` proxies
it straight through to the browser rather than building its own cookie from a token in the body.
Cookie policy lives in exactly one place (`api/cookies.py`) as a result.

## Login throttling and user enumeration

- **One limiter type, two instances** (`throttle.py`, `AttemptThrottle`): an in-process,
  in-memory sliding-window counter keyed by `(subject, client_ip)`. Chosen over a
  `login_attempts` table or Redis because it needs no schema change and no new infrastructure for
  a hobby-project deployment with one uvicorn worker.
  - **Login** (`app.state.login_throttle`, subject = normalized email): 5 *failures* in 15
    minutes; a correct password resets the key.
  - **Signup** (`app.state.signup_throttle`, subject = a fixed sentinel — there's no account yet
    to key on): 10 *attempts* per hour per source. Every attempt counts, not just the 409s,
    because a successful signup pays the same Argon2 cost (64 MiB, 40 threads) an abusive one
    does — counting only failures would bound the enumeration oracle while leaving the
    CPU/memory-exhaustion vector wide open. Deliberately looser than login's, since it exists to
    bound abuse rather than to protect a specific account.
- **The `client_ip` half must be the browser's address, and only `web` can supply it.** `auth` is
  never reachable from outside the private network, so `request.client.host` is `web`'s container
  address on *every* request — throttling on it collapses each key to its subject alone, which
  would let anyone lock a known account out of login with five wrong guesses. `api/deps.py`'s
  `client_ip()` therefore reads the left-most `X-Forwarded-For` entry, falling back to
  `request.client.host`. **Trusting that header is sound here specifically** because every route
  using it sits behind the router-level internal-secret dependency: by the time `client_ip()`
  runs, the caller has already proved it is an internal service. **This is a contract on `web`
  (task 10): it must forward the browser's address as `X-Forwarded-For`.** Until it does, both
  throttles degrade to one shared bucket per `auth`-facing caller rather than becoming a lockout
  vector.
- **Residual gap, accepted**: throttling bounds signup enumeration but does not remove it — a
  `409` still distinguishes a registered email from a free one. Closing it properly means an
  email-verification flow (always answer `201`, confirm out of band), which is out of scope until
  there is real mail delivery to build it on.
- **Known limitation, accepted for now**: both limiters are correct only with a single worker
  process, and the map resets on every restart — a determined attacker who can force a restart,
  or a deployment that scales to multiple workers, gets a fresh limiter for free. **Trigger to
  revisit**: moving to more than one `auth` worker process, or before this is reachable by anyone
  outside a single trusted host — at that point move the counters to a shared store (Redis, or a
  `login_attempts` table, which would need schema sign-off per
  [`data-schema.md`](../../../.claude/rules/data-schema.md)).
- **User enumeration**: an unknown email on `/login` still runs a full Argon2 verification
  (`passwords.dummy_verify()`) against a hash generated once at import time, so response timing
  can't distinguish "no such user" from "wrong password" — both return the same `401`.

## Transaction boundaries

**The calling service owns the transaction, not the repository.** `users/repository.py` and
`sessions/repository.py` execute SQL and nothing else; `service.py` (and `jobs.py`, for the purge
sweep) wraps each unit of work in `shared.db.connection.transaction`.

This is a correction, not the original shape: with each repository function opening its own
`transaction()`, `signup` committed the user row and the session row separately — a failure
between them left a durable account whose owner had never been logged in and could never sign up
again, because every retry hits the `409`. `transaction()` commits on exit and does not nest, so
there was no way to compose the two calls into one unit of work without moving the boundary up.

Consequences worth knowing:

- A repository call on its own does **not** persist anything. `psycopg` opens a transaction
  implicitly, so a caller that forgets `transaction()` silently rolls back when the connection is
  returned to the pool. `validate_session` needs one for that reason even though it reads like a
  lookup: `renew_and_fetch`'s `UPDATE` slides the session's expiry.
- `signup` catches `UniqueViolation` *outside* the `with transaction(...)` block, so the rollback
  runs first and the connection is left usable — Postgres aborts the whole transaction on a failed
  statement, and every later statement on that connection would fail until something rolls it back.

## Entry points

- `auth serve` (`cli.py` → `api/app.py`) — runs the HTTP API via `uvicorn`, host/port from
  `settings.py` (`AUTH_API_HOST`/`AUTH_API_PORT`).
- `auth sessions purge-expired` (`cli.py` → `jobs.py`) — deletes sessions past `expires_at` or
  `absolute_expires_at`, using the `(expires_at)` index `docs/schema.md` describes existing for
  exactly this. Same `{"status", "rows_saved"}` contract as `auth db init`.
- `auth db init` — unchanged from task 1.

## Deployment

`auth-postgres` is a dedicated Postgres 17 container (`services/auth/docker-compose.yml`), separate
from `data-store-postgres` and the future `app-postgres` — see
[`portfolio`'s deployment section](../../portfolio/docs/architecture.md#deployment) for the
three-instances-not-two reasoning. Connection config is `AUTH_DATABASE_URL` (`settings.py`),
deliberately not the bare `DATABASE_URL` `data-store` already owns — the two services' integration
suites can run in the same pytest process, where a shared env var name would mean whichever
service's `.env` loads first silently wins for both. `auth db init` (`cli.py` → `jobs.py`) applies
`db/schema.sql`, mirroring `data-store`'s CLI/jobs layering exactly. The connection/transaction
helpers and log setup underneath both are the *same* code, not just the same pattern — both
services depend on [`services/shared`](../../shared/docs/architecture.md) rather than each keeping
its own copy.

**`auth-postgres` runs with `log_statement=mod`** (`docker-compose.yml`'s `command:`), logging
every `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` to the container's own log — including a direct `psql`
connection that bypasses `auth`'s own code entirely. Added after an incident where a `users` row
(and its cascaded `sessions` row) vanished with no application code path capable of deleting
either, and no way to reconstruct what happened after the fact. This is container-log-level
forensics, not a durable audit table — a real `audit_log` table with triggers was considered and
deferred, since it's a schema change needing its own sign-off per `data-schema.md`.

Three settings alongside `log_statement=mod` close gaps the initial version of this logging had:

- **`log_line_prefix=%m [%p] %q%u@%d `** adds the connecting role and database to every line.
  Without it a logged line carries only a timestamp and OS PID — enough to see *what* ran, not
  *who* ran it, which defeats the forensic point of turning this on. (`auth-api` and any direct
  `psql` session connect as the same `AUTH_POSTGRES_USER` today, so this doesn't yet distinguish
  app traffic from an operator bypassing it — only a per-operator role would. Flagged, not fixed:
  that's a role-model change, out of scope here.)
- **`log_connections=on` / `log_disconnections=on`** record sessions that open and close without
  ever running a statement `log_statement=mod` would catch — relevant for reconstructing a direct
  `psql` connection that only ran `SELECT`s before whatever it did next.
- **`log_parameter_max_length=0`** keeps the statement template in the log (`INSERT INTO users
  (email, ...) VALUES ($1, $2)`) but drops the bound parameter values. Without it, `log_statement
  =mod` logs every bind parameter verbatim in a `DETAIL:` line — for `auth` that means every
  signup/login would otherwise write the user's plaintext email into the container log, a new
  place PII lives that this change didn't originally account for.

Logs still rotate with Docker's log driver if not shipped somewhere durable; `docker-compose.yml`
caps the default `json-file` driver at `max-size: 10m` / `max-file: 5` per container so turning
this on doesn't grow unboundedly on a long-lived host — it does not make the logs durable, only
bounded.

`portfolio`'s `_ensure_role_and_schema` (`services/portfolio/src/portfolio/jobs.py`) temporarily
sets `log_statement=none` for the one transaction that runs `CREATE ROLE ... PASSWORD`, since that
password is a SQL literal, not a bind parameter — `log_parameter_max_length` has no effect on it,
and it would otherwise be logged in full on every fresh environment's first `db init` run.

Two further containers share that one image (`services/auth/Dockerfile`), built from the repo
root the same way `data-store`'s `Dockerfile.airflow` is, since `uv sync` needs every workspace
member's source present even though the image only runs `auth`'s own entry points:

- `auth-db-init` runs `auth db init` and exits. A one-shot compose service (`api` waits on
  `service_completed_successfully`) rather than a step in the API's entrypoint, because the schema
  is idempotent but not concurrency-safe — applying it once, before anything serves, keeps it that
  way even if `api` is ever scaled past one replica. Without this step nothing applied the schema
  at all: the stack came up and every endpoint `500`ed on `relation "users" does not exist`.
- `auth-api` runs `auth serve`, and its healthcheck hits `/health`, which probes for both tables
  (`to_regclass`, a catalogue lookup rather than a scan) — so a container pointed at an
  uninitialised database reports unhealthy instead of accepting traffic it can't serve.

The repo-root `.dockerignore` excludes `**/.data/` for every service, not just `data-store`'s: the
bind-mounted Postgres data directory is created root-owned by the database container, so once
`docker compose up -d postgres` has run, any build that sends it as part of the context fails
outright with `error from sender: ... permission denied`.

## Talks to

- **`web`** calls `auth` server-to-server for login, logout, signup, and session validation, over
  the private internal network and carrying the shared internal secret (see "Key decisions").
- **`portfolio`** references auth-issued UUIDs as a trusted plain column but does not call `auth`
  directly — it has no local `users` table to keep in sync.

## Open questions

- Whether bearer-token support will ever be needed alongside cookie sessions — tied to whether
  `web` ever needs to serve non-browser clients.
- **Signed identity assertions (the service-to-service trust upgrade path)**: today `portfolio`
  trusts a forwarded `user_id` as a plain value, backed only by network isolation and the shared
  internal secret above — not distinguished from any other request an internal caller with the
  secret could make. The stronger fix — `auth`'s session-validate response including a short-lived,
  `auth`-signed assertion (e.g. a JWT with `user_id` + expiry) that `portfolio` verifies instead of
  trusting the raw value — is deliberately not built yet: it adds key distribution/rotation with
  nothing at stake to justify it currently. **Trigger to revisit**: before this deployment is ever
  reachable beyond a single trusted host, or before real user funds/PII are involved.
- **Failed-login throttle storage** — see "Login throttling and user enumeration" above; the
  in-memory limiter is correct only for a single worker process.

Closed by task 2: exact API contracts (see "Endpoint surface" above), and the
"bootstrapping before `auth` has a real signup endpoint" gap — `portfolio` (task 4+) can now get a
real UUID from `POST /signup` instead of an ad hoc one.
