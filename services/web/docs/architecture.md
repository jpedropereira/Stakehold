# web architecture

**Status: foundation and auth pages (task 10) implemented.**

## Responsibility

`web` is the user-facing surface of Stakehold. It renders the application in the browser and
calls [`portfolio`](../../portfolio/docs/architecture.md), `data-store`, and
[`auth`](../../auth/docs/architecture.md) on the user's behalf — it owns no data of its own.

## Stack

- **FastAPI + Jinja2** — server-rendered HTML, not a separate JS/TS SPA.
- **htmx**, vendored into `static/vendor/htmx.min.js` rather than loaded from a CDN — the app
  works offline and needs no script-src CDN exception in the Content-Security-Policy. Used today
  for auth forms (`hx-post` swapping an error partial); the same routes render a full page when
  the `HX-Request` header is absent, so the forms work with JS off.
- **Plain CSS** with custom properties (`--color-*`, `--font-*`) for the brand tokens — no
  Tailwind, no component kit, no CSS framework. `static/web.css` references only
  `var(--…)` tokens from `brand/tokens.css`, enforced mechanically by
  `tests/unit/test_templates.py` (no hardcoded hex value should ever land here).
- **Sync route handlers, not `async def`.** Matches `auth`'s documented shape
  (`auth/docs/architecture.md`, "Stack"): a sync `httpx.Client` opened once in the FastAPI
  lifespan, plain `def` routes, FastAPI running them in its threadpool. `web`'s per-request work
  is one or two short calls to `auth`, not a hot path that benefits from async.
- A lightweight JS charting library for returns/allocation views is still future work — task 12,
  not this one.

## Key decisions

- **Server-rendered, not an SPA; `auth`'s session cookie is proxied, not reissued.** `auth` sets
  `Set-Cookie` itself (`auth/api/cookies.py`); `web`'s `AuthResult` (`auth_client/base.py`)
  carries that raw header value, and every route that receives one copies it onto its own
  response rather than rebuilding a cookie from a token in the response body. Cookie policy stays
  decided in exactly one place, in `auth`.
- **`AuthClient` is a Protocol (`auth_client/base.py`) with one real implementation
  (`auth_client/http_client.py`)** — the same split `portfolio`'s `DataStoreClient` uses. This is
  what makes `tests/unit/test_routes.py` runnable against a fake with no live `auth` service, and
  keeps HTTP status-code mapping in exactly one place.
- **CSRF: a stateless double-submit cookie (`api/csrf.py`)**, not a signed/itsdangerous token.
  A random value is set in a cookie and re-embedded as a hidden form field; a POST is accepted
  only if the two match (`secrets.compare_digest` on encoded bytes, mirroring
  `auth/api/deps.py`'s `require_internal_secret` reasoning: comparing raw strings would raise
  `TypeError` on a non-ASCII forged value and turn a rejected request into a 500). No secret key
  or server-side store is needed: a cross-site forged POST can neither read the victim's cookie
  value nor have a `SameSite=Lax` cookie attached to a cross-site POST in the first place, so it
  can never submit a matching pair. The cookie name follows `cookie_secure`
  (`__Host-stakehold_csrf` vs. plain `stakehold_csrf`) because the `__Host-` prefix itself
  requires `Secure` — a fixed name would silently fail every local http request.
- **`client_ip` trusts only the raw socket address, never an inbound `X-Forwarded-For`
  (`api/deps.py`).** `web` is the public edge of the deployment; honoring a forwarded header here
  would let any caller spoof the address `auth`'s login/signup throttles key on. This is the
  mirror image of `auth/api/deps.py`'s own `client_ip()`, which *does* trust that header —
  correctly, because by the time it runs the caller has already proved it is `web` via the
  internal-secret check. When a real reverse proxy is added in front of `web`, the fix is
  uvicorn's `--proxy-headers` plus a `forwarded_allow_ips` allowlist, not trusting the header
  unconditionally from any source.
- **`web` sets `X-Forwarded-For` on every signup/login call to `auth`**, carrying `client_ip`'s
  result — the contract `auth/docs/architecture.md` describes under "Login throttling and user
  enumeration". Without it, every request `auth` sees looks like it came from `web`'s own
  container address, collapsing both throttles to one shared bucket.
- **`/health` does not probe `auth`.** Unlike `auth`'s own `/health` (which checks its schema),
  `web`'s is a plain `{"status": "ok"}` with no dependency check. `web` is meaningfully "up" —
  able to serve the login page, its own static assets — independent of whether `auth` currently
  is, and a dependent healthcheck would make a transient `auth` blip restart `web` too.
- **Security headers as middleware (`api/security_headers.py`)**: `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and a CSP allowing `'self'`
  plus `fonts.googleapis.com`/`fonts.gstatic.com` (`brand/tokens.css` `@import`s Google Fonts).
  No script-src exception is needed since htmx is vendored rather than CDN-loaded.
- **Brand assets are mounted, not copied.** `brand/` (tokens, logos) is mounted as `StaticFiles`
  at `/brand`, from a `WEB_BRAND_DIR` setting defaulting to the repo-root `brand/` directory
  resolved relative to `settings.py`. The Docker image instead sets `WEB_BRAND_DIR` to a path it
  explicitly `COPY`s the directory to. Either way there is exactly one `brand/tokens.css` on
  disk — CLAUDE.md's "import it once" holds literally, not just by convention.
- **`web` is the only client of `portfolio`/`data-store`/`auth`, for now.** Unchanged from before
  task 10 — still what justifies `auth`'s cookie-based (rather than bearer-token) session model.

## Endpoint surface

| Route | Behaviour |
|---|---|
| `GET /` | `require_user` dependency: logged in → the placeholder home page; no valid session → redirected to `/login` (an `AuthRequired` exception mapped to a 303 by an app-level handler) |
| `GET /login`, `GET /signup` | `optional_user` dependency: already logged in → redirected to `/`; otherwise renders the form with a fresh or existing CSRF cookie |
| `POST /login`, `POST /signup` | CSRF-checked, calls `auth`, proxies the `Set-Cookie` `auth` returned, then redirects to `/` — a 303 for a plain form post, or an `HX-Redirect` response header for an `hx-post` (htmx's own XHR follows a 303 by swapping the *content* of the request's own response into the target element, which would drop a full HTML document into a form's error slot; `HX-Redirect` instead triggers a real browser navigation) |
| `POST /logout` | CSRF-checked, calls `auth` (best-effort — a failure is logged, not surfaced, since the user's intent to log out should not depend on `auth` being reachable), clears the session cookie locally, redirects to `/login` |
| `GET /health` | `200 {"status": "ok"}`, unauthenticated, no dependency check (see "Key decisions") |

Every POST error (wrong credentials, a duplicate email, a tripped throttle, `auth` being
unreachable, or a CSRF mismatch) re-renders the form with a flat, specific message — chapter IV
of the brand charter's "no panic, no hype" applies to error copy as much as to figures. A wrong
password and an unknown email deliberately render the same message, so `web` doesn't reintroduce
the enumeration oracle `auth`'s own `passwords.dummy_verify()` exists to close.

## Modules

- `settings.py` — `WEB_AUTH_API_URL`, `WEB_INTERNAL_SECRET`, `WEB_BRAND_DIR`,
  `WEB_COOKIE_SECURE`, `WEB_API_HOST`/`WEB_API_PORT`, `WEB_HTTP_TIMEOUT_SECONDS`. Same
  `SettingsError`/`_int_env`/`_bool_env` shape as `auth/settings.py`, including the
  set-but-empty-falls-back-to-default handling that keeps an interpolated-empty compose variable
  from silently stripping `Secure`.
- `auth_client/{base,http_client,errors}.py` — the `AuthClient` Protocol, its real
  implementation, and one error hierarchy (`AuthClientError`) routes catch with a single `except`.
- `api/{app,deps,routes,csrf,security_headers}.py` — app assembly and lifespan (opens/closes the
  shared `httpx.Client`), FastAPI dependencies (`require_user`, `optional_user`, `client_ip`,
  and friends), the route handlers, the CSRF helpers, and the security-headers middleware.
- `templates/`, `static/` — Jinja2 templates (`base.html` plus one per page,
  `partials/_form_errors.html` for the htmx swap target) and `web.css` plus vendored `htmx.min.js`.
- `cli.py` — `web serve` only, mirroring `auth serve`'s lazy-import-uvicorn shape.

## Deployment

`services/web/Dockerfile` mirrors `services/auth/Dockerfile`'s reasoning exactly: built from the
repo root (not `services/web`) so `uv sync --frozen` can resolve the whole workspace lock, since
root `pyproject.toml` depends on every service. It additionally `COPY`s `brand/` and sets
`WEB_BRAND_DIR` to that baked-in path — brand assets are a build-time snapshot here, same as
application code, not mounted live.

`services/web/docker-compose.yml` currently starts `web` alone. It deliberately does **not** join
`auth` onto a shared network with an in-network URL yet — `WEB_AUTH_API_URL` in `.env` must point
at wherever `auth`'s own compose stack already publishes its API. Wiring all three services (plus
`portfolio`, once it exists) onto one network with in-network addresses is task 13's job; doing
it here piecemeal would leave two half-finished topologies to reconcile later instead of one
deliberate one.

## Talks to

- **`auth`** — login, logout, signup, and per-request session validation calls, server-to-server,
  carrying `WEB_INTERNAL_SECRET` as `X-Internal-Secret` and the browser's address as
  `X-Forwarded-For` on signup/login.
- **`portfolio`** — transactions, positions, and returns. Not yet called: task 9 (the portfolio
  HTTP API) doesn't exist yet, so this is task 11's work.
- **`data-store`** — market data reads, once a UI view needs them (task 12).

All calls are server-to-server; the user's session identity is forwarded as needed, never exposed
to browser JS.

## Open questions

- **`require_user` cannot distinguish "logged out" from "`auth` is unreachable"** — both send the
  visitor to `/login` with no explanation. Acceptable for a hobby-scale deployment where `auth`
  being down is rare and short; revisit if that stops being true.
- Whether `web` will ever need to serve non-browser clients, which would push `auth` toward also
  supporting bearer tokens alongside cookie sessions.
- **Reverse-proxy `X-Forwarded-For` handling**: `client_ip()` reads only the raw socket address
  today. Once a real reverse proxy sits in front of `web`, the correct fix is uvicorn's
  `--proxy-headers` plus a `forwarded_allow_ips` allowlist — not trusting an inbound
  `X-Forwarded-For` unconditionally, which would let any external caller spoof it.
- Exact API contracts with `portfolio` and `data-store` — neither service's HTTP API exists yet
  (portfolio's is task 9).
- **Service-to-service trust**: resolved for now via network isolation plus a shared internal
  secret — see [`auth`'s architecture doc](../../auth/docs/architecture.md#key-decisions). The
  stronger fix (signed identity assertions from `auth`) is deliberately deferred; see that doc's
  "Open questions" for the trigger to revisit it.

Closed by task 10: deployment shape for `web`'s own container (see "Deployment" above, though the
multi-service network topology is still task 13's); the exact `auth` API contract (see "Endpoint
surface" and `auth/docs/architecture.md`'s own "Endpoint surface").
