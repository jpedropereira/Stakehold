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
| `POST /signup` | `201` + `Set-Cookie` | body `{user_id, email, display_name}`; `409` on a duplicate email |
| `POST /login` | `200` + `Set-Cookie` | `401` on wrong password or unknown email (identical response either way — see "user enumeration" below); `429` once the login throttle trips |
| `POST /sessions/validate` | `200` + refreshed `Set-Cookie` | reads the `Cookie` header; `401` on missing/expired/unknown session |
| `POST /logout` | `204` + cookie cleared | idempotent — `204` even for a missing or unrecognized token |
| `GET /health` | `200` | unauthenticated; backs the container healthcheck |

`auth` sets the `Set-Cookie` header itself (name `stakehold_session`, flags below) — `web` proxies
it straight through to the browser rather than building its own cookie from a token in the body.
Cookie policy lives in exactly one place (`api/cookies.py`) as a result.

## Login throttling and user enumeration

- **Failed-login throttle**: an in-process, in-memory sliding-window limiter (`throttle.py`,
  `LoginThrottle`), keyed by `(normalized_email, client_ip)` — 5 failures in 15 minutes locks that
  pair out for the rest of the window. Chosen over a `login_attempts` table or Redis because it
  needs no schema change and no new infrastructure for a hobby-project deployment with one uvicorn
  worker. **Known limitation, accepted for now**: correct only with a single worker process, and
  the map resets on every restart — a determined attacker who can force a restart, or a deployment
  that scales to multiple workers, gets a fresh limiter for free. **Trigger to revisit**: moving to
  more than one `auth` worker process, or before this is reachable by anyone outside a single
  trusted host — at that point move the counters to a shared store (Redis, or a
  `login_attempts` table, which would need schema sign-off per
  [`data-schema.md`](../../../.claude/rules/data-schema.md)).
- **User enumeration**: an unknown email on `/login` still runs a full Argon2 verification
  (`passwords.dummy_verify()`) against a hash generated once at import time, so response timing
  can't distinguish "no such user" from "wrong password" — both return the same `401`.

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

A second container, `auth-api` (`services/auth/Dockerfile`), runs `auth serve` — built from the
repo root the same way `data-store`'s `Dockerfile.airflow` is, since `uv sync` needs every
workspace member's source present even though this image only runs `auth`'s own entry point.

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
