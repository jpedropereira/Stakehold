# auth architecture

**Status: scaffolding — not yet implemented.**

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

## Key decisions

- **Stayed in Python/FastAPI rather than an off-the-shelf identity provider** (Keycloak,
  Authentik, Ory). Those are reasonable once there's a real need for MFA or social login, but
  excess complexity for a solo hobby project with no users yet.
- **Owns the only `users` table in the application**: `users(id UUID, email, password_hash,
  created_at)`, conceptually — column-level DDL still needs its own sign-off pass per
  [`data-schema.md`](../../../.claude/rules/data-schema.md). `id` is a UUID v7 generated at signup,
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
- **Session storage**: a `sessions` table in `auth`'s own Postgres, roughly `sessions(id UUID,
  user_id UUID, created_at, expires_at, last_seen_at)`, with a short-ish expiry (on the order of
  7–14 days) and sliding renewal on validation.
- **Cookie flags**: `httpOnly`, `Secure`, `SameSite=Lax`.
- **Service-to-service trust, resolved for now**: `auth` and `portfolio` are not reachable from
  outside the deployment's private network — only `web` is publicly exposed — and every internal
  call additionally carries a shared secret (an env-var-configured bearer token) that the receiving
  service checks before processing the request. This proves "a legitimate internal caller," not
  cryptographically that a specific `user_id` came from `auth` — an accepted gap at this stage, see
  the signed-assertion upgrade path under "Open questions."

## Expected endpoint surface

At minimum: login, logout, signup, session validation. Listed here as the expected surface, not
full request/response schemas.

## Talks to

- **`web`** calls `auth` server-to-server for login, logout, signup, and session validation, over
  the private internal network and carrying the shared internal secret (see "Key decisions").
- **`portfolio`** references auth-issued UUIDs as a trusted plain column but does not call `auth`
  directly — it has no local `users` table to keep in sync.

## Open questions

- Deployment shape for `auth` is undecided — `data-store` is the only service with a deployment
  story so far.
- Whether bearer-token support will ever be needed alongside cookie sessions — tied to whether
  `web` ever needs to serve non-browser clients.
- Exact API contracts and request/response schemas — not designed yet, just the expected
  endpoint surface above.
- **Signed identity assertions (the service-to-service trust upgrade path)**: today `portfolio`
  trusts a forwarded `user_id` as a plain value, backed only by network isolation and the shared
  internal secret above — not distinguished from any other request an internal caller with the
  secret could make. The stronger fix — `auth`'s session-validate response including a short-lived,
  `auth`-signed assertion (e.g. a JWT with `user_id` + expiry) that `portfolio` verifies instead of
  trusting the raw value — is deliberately not built yet: it adds key distribution/rotation with
  nothing at stake to justify it currently. **Trigger to revisit**: before this deployment is ever
  reachable beyond a single trusted host, or before real user funds/PII are involved.
- Whether to add an absolute session lifetime cap alongside the inactivity-based sliding renewal,
  so a continuously-used session can't live forever.
- **Bootstrapping before `auth` has a real signup endpoint**: `portfolio` needs a real UUID to put
  in `accounts.user_id` today, and `auth` doesn't have working signup yet. Until it does, that UUID
  has to come from somewhere ad hoc (a manually-generated UUID, a test fixture) — a known, temporary
  gap, not a design decision either service has made.
