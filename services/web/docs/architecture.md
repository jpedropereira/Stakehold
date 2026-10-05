# web architecture

**Status: foundation, auth pages (task 10), portfolio entry UI (task 11), and portfolio view UI
(task 12) implemented.**

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
- **`async def` route handlers and dependencies**, backed by one `httpx.AsyncClient` opened in
  the FastAPI lifespan. Every route ultimately does at least one `auth` call (`require_user`/
  `optional_user` alone means that's true even for `GET /`), and FastAPI dispatches every sync
  route/dependency through Starlette's shared threadpool -- capped at a fixed number of OS
  threads for the whole process (AnyIO's default `CapacityLimiter`), regardless of how idle the
  event loop is. Async keeps each request's wait on `auth` bound to one coroutine instead of one
  of those threads, so concurrent visitors aren't capped by that pool. (`auth` itself stays sync
  for now -- its own per-request work is a handful of local Postgres queries, not an outbound
  call to another service, so the same argument doesn't carry over; see
  `auth/docs/architecture.md`, "Stack".)
- **Chart.js** (task 12), vendored into `static/vendor/chart.min.js` — the same htmx precedent:
  no CDN `<script src>`, so no `script-src` CSP exception is needed (see "Key decisions" below).

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
- **`HttpAuthClient` always sends an explicit (possibly empty) `Cookie` header (`auth_client/
  http_client.py`, `_headers`).** The one `httpx.AsyncClient` in `app.state` is shared by every
  concurrent visitor; httpx's client-level cookie jar auto-stores every `Set-Cookie` it sees and
  auto-attaches it to the next outgoing request that doesn't set its own `Cookie` header. Without
  this, a request racing right after another visitor's login/signup response could carry that
  visitor's session token to `auth`. Setting the header (even to `""`) makes the underlying
  `http.cookiejar` skip the jar for that request; `validate_session`/`logout` override it with the
  browser's real cookie.
- **`login`/`validate_session` disambiguate a 401 by `auth`'s response `detail` text
  (`auth_client/http_client.py`).** `auth` answers both a wrong password/expired session *and* a
  missing/wrong `X-Internal-Secret` with a bare 401 — collapsing them would report a drifted
  internal secret to the visitor as "wrong password" instead of surfacing the real outage. This is
  a soft coupling to `auth`'s exact hard-coded strings (`auth/api/app.py`'s exception handlers);
  if those wordings change, this needs to change with them. The safe failure mode if they drift
  apart is `AuthUnavailable`, not a wrong domain error, so a mismatch degrades to "can't reach the
  sign-in service" rather than a misleading message.
- **`PortfolioClient` (`portfolio_client/{base,http_client,errors}.py`, task 11) is a copy of
  `auth_client`'s shape** — a Protocol plus one real `httpx`-backed implementation — exactly as
  this document anticipated at task 9. It's simpler in one respect: `portfolio`'s own
  `errors.py` gives every domain exception a distinct HTTP status code (see
  `portfolio/docs/architecture.md`'s error-mapping table), so `HttpPortfolioClient` dispatches on
  status code alone rather than `auth_client`'s `detail`-string matching. It differs in another:
  `portfolio`'s 422s arrive in two shapes — `{"detail": "<message>"}` from a `PortfolioError`
  handler, or FastAPI's own `{"detail": [{"msg": ..., ...}, ...]}` from Pydantic request
  validation — normalized by one `_error_detail` helper into a single string. And unlike
  `HttpAuthClient`, no cookie-jar defence is needed: `portfolio` calls carry no browser session,
  `user_id` is sent explicitly as trusted input (matching `portfolio/docs/architecture.md`'s own
  "Conceptual entity model" stance), so the shared `httpx.AsyncClient` has nothing per-visitor to
  leak.
- **`InvalidPortfolioRequest`'s message is shown to the visitor verbatim, not mapped to canned
  copy.** `auth_client`'s errors carry fixed, hand-written messages per type
  (`_LOGIN_ERRORS`/`_SIGNUP_ERRORS`) because a wrong-password message doubles as user-enumeration
  defence. Nothing like that applies to a portfolio validation failure (a bad `fx_rate`, an
  unmapped broker, a malformed export row) — the server's own message is specific and actionable,
  so `_submit_portfolio_form` special-cases `InvalidPortfolioRequest` to render `str(exc)`
  directly rather than looking it up in a `error_messages` dict.
- **A successful manual-transaction add or file import redirects back to the same form**
  (`/transactions/new?account=<label>&saved=1`, `/transactions/import?account=<label>&imported=<n>`)
  rather than to the accounts hub — the first new redirect-target convention beyond task 10's
  always-go-home shape. Chosen to support entering several transactions for one account in a row
  without re-navigating each time; the query flag is read back by the same `GET` route to render a
  plain confirmation banner (`rows_saved`/`imported` in `--font-mono`/`--text-figure`, per the
  brand charter's numbers-only-in-mono rule).
- **`account_label` is always a query parameter or a hidden form field in `web` too, never a path
  segment** — the same reasoning `portfolio`'s own API documents (a label is free text that may
  contain `/`), applied here even though nothing forces `web`'s own routing to follow it.
- **`Form(...)` (a required FastAPI form field) treats an empty string as *missing*, not
  present-but-blank**, in the FastAPI/Starlette versions this repo pins — confirmed directly
  (`services/web/src/web/api/routes.py`'s account/transaction/import submit routes). A route that
  wants blank input to reach its own CSRF check and `portfolio`'s own validation, rather than a
  raw framework 422, must declare that field `Form(default="")` instead. `login_submit`/
  `signup_submit` (task 10) predate this finding and still use `Form(...)`; revisit if a blank
  email/password field is ever reported reaching the visitor as a raw JSON 422 instead of the
  intended form-error page.
- **Portfolio-level return aggregation is `web`'s own problem, solved in a new pure module
  (`portfolio_view.py`, task 12), not pushed onto `portfolio`.** `portfolio`'s `GET /returns` is
  per-ticker (`portfolio/docs/architecture.md`'s "Returns are served per ticker" note) — different
  tickers can have different trading-day calendars, so summing across them isn't a serialization
  detail. `aggregate_portfolio_returns` builds the sorted union of every `price_date` across every
  ticker in one account's `/returns` response, then walks each ticker's own series forward in
  lockstep with that union: a ticker missing its own row on a union date has its last-known row
  forward-filled (not priced that day ≠ worth zero that day), while a date *before* a ticker's
  first-ever row gets no contribution from it at all (not held/priced yet ≠ priced at zero). Per
  date, `market_value_eur`/`cost_basis_eur`/`dividends_received_eur`/`total_return_eur` are summed
  across contributing tickers, and `total_return_pct` is **recomputed** as
  `sum(total_return_eur)/sum(cost_basis_eur)` — never averaged from the per-ticker percentages,
  since those don't sum to a portfolio percentage. A companion `filter_open_positions` drops
  `quantity == 0` rows from `GET /positions`'s response (which intentionally doesn't prune closed
  positions) for the "current holdings" table, but keeps a negative quantity visible — that's a
  replay anomaly `positions/calculator.py` warns about but still computes, not a modeled short
  position, and hiding it would hide the thing a user most needs to notice.
- **Chart.js is vendored (`static/vendor/chart.min.js`), and the chart reads its colors from
  brand tokens at runtime rather than from a hardcoded hex value.** `script-src 'self'` (no CDN
  exception, no `unsafe-inline`, no nonce) rules out both a CDN `<script src>` and an inline
  `<script>` block with real logic — this codebase has never had one, and adding the first here
  would either be silently blocked by the browser or force a CSP weakening this task's own
  no-CDN framing argues against. The chart's numeric data is embedded via a non-executable
  `<script type="application/json">` data island in `portfolio.html` (inspected as data, not run
  as script — needs no `script-src` permission to be read); the logic (`getComputedStyle(...)
  .getPropertyValue('--forest')` etc., and the `Chart` instantiation) lives in a same-origin
  `static/portfolio_chart.js`, loaded exactly like vendored htmx. `test_templates.py`'s new
  `test_templates_have_no_inline_executable_script` makes the "no inline `<script>` with logic"
  invariant mechanical rather than only reviewed by eye. The chart uses a `category` x-axis, not
  Chart.js's `time` scale, to avoid vendoring a second date-adapter dependency just to plot daily
  points — a calendar gap renders as an equal-width step rather than a proportionally wider one,
  an accepted v1 simplification.
- **`GET /portfolio` is per-account, read-only, and never triggers a rebuild.** Every `portfolio`
  positions/returns endpoint is `account_label`-scoped with no cross-account aggregation endpoint,
  so the view is one page per account (linked from `accounts.html`); a cross-account "whole
  portfolio" rollup is future work. The route only displays whatever `position_snapshots`/
  `return_snapshots` rows already exist — `rebuild-positions`/`rebuild-returns` stay CLI-only. An
  `AccountNotFound` from either the positions or returns call is treated as fatal (a distinct 404,
  "that account doesn't exist") rather than folded into the generic degrade-to-inline-error path
  `_list_accounts_and_brokers` (task 11) established, since a bad `account_label` has nothing
  partial to show either way; any other `PortfolioClientError` on either call degrades that half
  of the page independently, mirroring `_list_accounts_and_brokers` exactly. An account with no
  positions and/or no return history yet (nobody has run the CLI rebuild) renders as an ordinary
  empty state, not an error — `web` has no way to distinguish "never rebuilt" from "rebuilt into
  zero rows" and doesn't need to.

## Endpoint surface

| Route | Behaviour |
|---|---|
| `GET /` | `require_user` dependency: logged in → the placeholder home page; no valid session → redirected to `/login` (an `AuthRequired` exception mapped to a 303 by an app-level handler) |
| `GET /login`, `GET /signup` | `optional_user` dependency: already logged in → redirected to `/`; otherwise renders the form with a fresh or existing CSRF cookie |
| `POST /login`, `POST /signup` | CSRF-checked, calls `auth`, proxies the `Set-Cookie` `auth` returned, then redirects to `/` — a 303 for a plain form post, or an `HX-Redirect` response header for an `hx-post` (htmx's own XHR follows a 303 by swapping the *content* of the request's own response into the target element, which would drop a full HTML document into a form's error slot; `HX-Redirect` instead triggers a real browser navigation) |
| `POST /logout` | CSRF-checked, calls `auth` (best-effort — a failure is logged, not surfaced, since the user's intent to log out should not depend on `auth` being reachable), clears the session cookie locally, redirects to `/login` |
| `GET /health` | `200 {"status": "ok"}`, unauthenticated, no dependency check (see "Key decisions") |
| `GET /accounts` | `require_user`; lists the caller's accounts (`portfolio`'s `GET /accounts`) and available broker parsers (`GET /brokers`, for the create-account `<datalist>` hint) and renders the create-account form |
| `POST /accounts` | CSRF-checked, calls `portfolio`'s `POST /accounts`, redirects to `/accounts` on success |
| `GET /transactions/new` | `require_user`; required `account` query param (a bare 422 for a missing/blank value — reachable only via a hand-crafted URL, since every link that points here already includes it); renders the manual-entry form, with a confirmation banner if `?saved=1` |
| `POST /transactions/new` | CSRF-checked, calls `portfolio`'s `POST /transactions`, redirects to `/transactions/new?account=<label>&saved=1` on success |
| `GET /transactions/import` | `require_user`; required `account` query param; renders the upload form, with a confirmation banner (row count) if `?imported=<n>` |
| `POST /transactions/import` | CSRF-checked, multipart; size-capped at 20MB (matching `portfolio`'s own cap) before the bytes are forwarded; calls `portfolio`'s `POST /transactions/import`, redirects to `/transactions/import?account=<label>&imported=<rows_saved>` on success |
| `GET /portfolio` | `require_user`; required `account` query param; fetches `portfolio`'s `GET /positions`/`GET /returns` concurrently, aggregates the per-ticker return series into one portfolio-level series (`portfolio_view.aggregate_portfolio_returns`), and renders a positions table plus a Chart.js return-over-time chart. `AccountNotFound` on either call renders a 404; any other failure degrades that half of the page independently, mirroring `GET /accounts`'s own pattern. No writes, no rebuild trigger. |

Every POST error (wrong credentials, a duplicate email, a tripped throttle, `auth`/`portfolio`
being unreachable, a CSRF mismatch, or a `portfolio` validation failure) re-renders the form with
a flat, specific message — chapter IV of the brand charter's "no panic, no hype" applies to error
copy as much as to figures. A wrong password and an unknown email deliberately render the same
message, so `web` doesn't reintroduce the enumeration oracle `auth`'s own `passwords.dummy_verify()`
exists to close; no equivalent concern applies to the portfolio routes; see "Key decisions" above.

## Modules

- `settings.py` — `WEB_AUTH_API_URL`, `WEB_INTERNAL_SECRET`, `WEB_PORTFOLIO_API_URL`,
  `WEB_PORTFOLIO_INTERNAL_SECRET`, `WEB_BRAND_DIR`, `WEB_COOKIE_SECURE`,
  `WEB_API_HOST`/`WEB_API_PORT`, `WEB_HTTP_TIMEOUT_SECONDS`. Its own
  `SettingsError`, but the typed env readers (`int_env`/`float_env`/`bool_env`, including the
  set-but-empty-falls-back-to-default handling that keeps an interpolated-empty compose variable
  from silently stripping `Secure`) come from `shared.env` — the same functions `auth/settings.py`
  uses, extracted there after both carried a byte-for-byte copy.
- `auth_client/{base,http_client,errors}.py` — the `AuthClient` Protocol, its real
  implementation, and one error hierarchy (`AuthClientError`) routes catch with a single `except`.
- `portfolio_client/{base,http_client,errors}.py` (task 11, extended task 12) — the
  `PortfolioClient` Protocol, its real implementation, and one error hierarchy
  (`PortfolioClientError`); see "Key decisions" for how and why it departs from `auth_client`'s
  shape in a couple of places. Task 12 added `list_positions`/`list_returns` (plus `Position`/
  `Return` dataclasses) to the same trio rather than inventing new files — no new error types were
  needed, since `AccountNotFound`/`PortfolioUnavailable` already cover every failure mode these
  two reads can produce.
- `portfolio_view.py` (task 12) — pure, I/O-free reshaping of what `routes.py` fetches for
  `GET /portfolio`: `filter_open_positions` and `aggregate_portfolio_returns` (see "Key
  decisions"). No FastAPI/httpx dependency, tested in isolation, mirroring how `portfolio`'s own
  `returns/calculator.py` is a pure function.
- `api/{app,deps,routes,csrf,security_headers,htmx}.py` — app assembly and lifespan (opens/closes
  two shared `httpx.AsyncClient`s, one per upstream service), FastAPI dependencies (`require_user`,
  `optional_user`, `client_ip`, `get_auth_client`/`get_portfolio_client`, and friends), the route
  handlers, the CSRF helpers, the security-headers middleware, and `htmx.py`'s
  `is_htmx`/`htmx_or_redirect` (shared by `routes.py`'s redirects and `app.py`'s `AuthRequired`
  handler, so every redirect in the app picks htmx-partial vs. full-navigation the same way).
- `templates/`, `static/` — Jinja2 templates (`base.html` plus one per page,
  `partials/_form_errors.html` for the htmx swap target) and `web.css` plus vendored `htmx.min.js`.
- `cli.py` — `web serve` only, mirroring `auth serve`'s lazy-import-uvicorn shape.

## Deployment

`services/web/Dockerfile` mirrors `services/auth/Dockerfile`'s reasoning exactly: built from the
repo root (not `services/web`) so `uv sync --frozen` can resolve the whole workspace lock, since
root `pyproject.toml` depends on every service. It additionally `COPY`s `brand/` and sets
`WEB_BRAND_DIR` to that baked-in path — brand assets are a build-time snapshot here, same as
application code, not mounted live.

`services/web/docker-compose.yml` on its own still starts `web` alone, pointed at whatever
`WEB_AUTH_API_URL`/`WEB_PORTFOLIO_API_URL` in `.env` say (by default, `localhost:8001`/`:8002` —
wherever `auth`'s/`portfolio`'s own standalone compose stacks publish their APIs). Task 13 added an
opt-in overlay, `services/web/docker-compose.stakehold-net.yml`, passed alongside the base file
with a second `-f`: it joins `web` onto `stakehold-net` (an externally-created Docker bridge
network — `docker network create stakehold-net`, once per machine) and overrides
`WEB_AUTH_API_URL`/`WEB_PORTFOLIO_API_URL` to `auth-api`'s/`portfolio-api`'s in-network addresses.
`auth` and `portfolio` gained matching overlay files of their own, joining their own `api`
containers (not `postgres`/`db-init`) to the same network. A single merged root compose file (via
`include:`) was tried and rejected: `auth`, `portfolio` and `data-store`'s compose files all name
their API service `api` — merging them into one project silently collapses three services into
one, since Compose merges by service key, not by file. The per-service overlay keeps every
service's own compose file byte-for-byte usable standalone, with no new prerequisite beyond the
one-time network creation. See `docs/smoke-test.md` for the full bring-up sequence.

## Talks to

- **`auth`** — login, logout, signup, and per-request session validation calls, server-to-server,
  carrying `WEB_INTERNAL_SECRET` as `X-Internal-Secret` and the browser's address as
  `X-Forwarded-For` on signup/login.
- **`portfolio`** (tasks 11–12) — account creation, manual transaction entry, broker-file import,
  and (task 12) reading positions/returns, server-to-server, carrying
  `WEB_PORTFOLIO_INTERNAL_SECRET` as `X-Internal-Secret`. Rebuild triggers (`rebuild-positions`/
  `rebuild-returns`) still aren't called from `web` — those stay CLI-only; see task 12's "Key
  decisions" entry.
- **`data-store`** — turns out not to be needed directly. This line previously anticipated task 12
  calling `data-store` for market-data reads; in practice `portfolio`'s own `/positions`/`/returns`
  already return EUR-converted figures (computed against `data-store` on `portfolio`'s side, at
  rebuild time), so the view built here has no reason to call `data-store` itself. Revisit only if
  a future view needs data `portfolio` doesn't already expose (e.g. a raw intraday price).

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
- Whether a future view will need data `portfolio` doesn't already expose, at which point `web`
  would need its own `data-store` client — not needed by task 12 (see "Talks to" above).
- **Cross-account portfolio rollup**: `GET /portfolio` is per-account only, matching every
  `portfolio` endpoint's own scope. A "total across every account" view would need a new
  `portfolio` endpoint (no such aggregation exists there today) before `web` could build one.
- **Service-to-service trust**: resolved for now via network isolation plus a shared internal
  secret — see [`auth`'s architecture doc](../../auth/docs/architecture.md#key-decisions). The
  stronger fix (signed identity assertions from `auth`) is deliberately deferred; see that doc's
  "Open questions" for the trigger to revisit it.

Closed by task 10: deployment shape for `web`'s own container (see "Deployment" above). Closed by
task 13: the multi-service network topology. The exact `auth` API contract (see "Endpoint
surface" and `auth/docs/architecture.md`'s own "Endpoint surface").

Closed by task 11: the exact `portfolio` API contract (see "Endpoint surface" above and
`portfolio/docs/architecture.md`'s own "HTTP API" section).

Closed by task 12: the charting library choice (Chart.js, vendored); the portfolio-level return
aggregation design (`portfolio_view.aggregate_portfolio_returns`); confirmed no direct `data-store`
call is needed for this view.
