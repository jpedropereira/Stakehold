# web architecture

**Status: foundation, auth pages (task 10), portfolio entry UI (task 11), portfolio view UI
(task 12), and dark mode (UI-3) implemented.**

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
- **Plain CSS** with custom properties — no Tailwind, no component kit, no CSS framework. Two
  token layers (UI-1):
  - *Brand tokens* (`--stone`, `--parchment`, `--gold`, `--font-*`, `--text-*`, …) live in
    `brand/tokens.css`, the brand's single source of truth.
  - *Semantic aliases* (`--surface`, `--surface-raised`, `--text`, `--text-muted`, `--border`,
    `--border-control`, `--accent`, `--on-accent`, `--gain`, `--loss`, `--focus-ring`) are
    defined in the
    `:root` block at the top of `static/web.css`. That block and the dark-theme blocks after it
    (see "Theme" in [`ui-architecture.md`](ui-architecture.md)) are the only places in the file a brand *color* token may
    appear. Every rule styles through the aliases, so the dark theme re-points about ten aliases
    instead of rewriting rules one by one.
    `--on-accent` exists separately from `--text` because text on a gold button stays stone in
    either theme, while body text flips. `--focus-ring` is `--gold-deep`, not gold: gold on
    parchment is about 2.2:1, under WCAG's 3:1 minimum for a focus indicator. UI-1 first used
    stone, which also passes. UI-2's signed-off charter chose deep gold so focus still reads as
    the brand accent. `--border-control` exists separately from `--border` for the same 3:1
    rule: `--border` is a decorative hairline (about 1.3:1), fine for dividers, but a form
    control's edge is what identifies the control. An alias is never named after the brand
    token it points at (`--x: var(--x)` is a CSS cycle, so the property becomes invalid), which
    is why the alias is `--border-control` and the brand token is `--control-border`.

  The same block holds a spacing scale (`--space-1`…`--space-8`, 4–64px) and `--content-max`.
  Pages are built from a few shared components (`.card` with `.card-narrow`/`.card-wide`,
  `.field` wrapping a label and its control, and `.btn` with `.btn-primary`/`.btn-ghost`) instead
  of per-page copies, and every interactive element gets a deep-gold `:focus-visible` outline.
  `tests/unit/test_templates.py` enforces all of this mechanically: no hardcoded hex value, no
  brand color token outside the theme blocks (the list is read from `tokens.css`, so new brand
  colors are covered automatically), no CSS class that no template uses, no template class
  that `web.css` doesn't define, and no form control bordered with `--border`.
  `tests/unit/test_brand_contrast.py` measures each alias against both surfaces for WCAG AA, in
  each theme, and rejects a self-referencing alias.
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
  no CDN `<script src>`, so no `script-src` CSP exception is needed (see [`ui-architecture.md`](ui-architecture.md)).

## Key decisions

Presentation decisions (theme, chart, display formatting, the app shell, form scripts, and
feedback and error states) are in [`ui-architecture.md`](ui-architecture.md), split out of
this file for size. It counts as part of this document under `.claude/rules/architecture.md`.

- **Server-rendered, not an SPA; `auth`'s session cookie is proxied, not reissued.** `auth` sets
  `Set-Cookie` itself (`auth/api/cookies.py`); `web`'s `AuthResult` (`auth_client/base.py`)
  carries that raw header value, and every route that receives one copies it onto its own
  response rather than rebuilding a cookie from a token in the response body. Cookie policy stays
  decided in exactly one place, in `auth`.
- **`AuthClient` is a Protocol (`auth_client/base.py`) with one real implementation
  (`auth_client/http_client.py`)** — the same split `portfolio`'s `DataStoreClient` uses. This is
  what makes `tests/unit/test_routes.py` runnable against a fake with no live `auth` service, and
  keeps HTTP status-code mapping in exactly one place.
- **Admin pages are gated here, in `web` (`api/deps.require_admin`).** `auth` owns the role
  (`users.is_admin`, granted only by the `auth user set-admin` CLI) and reports it on every
  session validation; `require_admin` builds on `require_user` and raises `AdminRequired`, which an
  app-level handler turns into a 403 page. The check happens on every request, against what
  `auth` just reported, so revoking admin takes effect on the next page load with no cached role
  to expire. `portfolio` doesn't re-check the role on the admin endpoints `web` will call: it
  already trusts its internal-secret caller for every user's data, and an admin check there would
  rest on the same trust (a forwarded flag), so it would add no protection the secret doesn't
  already give. `AuthClient` treats a missing or non-boolean `is_admin` as `False`, so the gate
  fails closed and a `web` deployed ahead of `auth` still lets everyone log in.
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
  No script-src exception is needed since htmx is vendored rather than CDN-loaded. The headers
  are set by one function, `apply_security_headers`, which the 500 handler also calls itself:
  Starlette runs an `Exception` handler in `ServerErrorMiddleware`, outside every user
  middleware, so that response would otherwise leave without the CSP (UI-12).
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
  brand charter's numbers-only-in-mono rule). Since UI-7 `saved` is the count
  (`transactions_saved`), not a flag, so `saved=0` (a reference already recorded) and `imported=0`
  say "nothing new" instead of "saved". The redirect also carries `portfolio`'s follow-on rebuild
  statuses as `positions=` and `returns=`, omitted when an older `portfolio` doesn't report them.
  The banner turns them into one sentence via `rebuild_outcome.py`. That sentence is chosen from
  fixed copy keyed on a whitelist, so a hand-edited URL can't put its own text on the page, and an
  unknown status just leaves the banner at "Transaction saved.". *Rejected: a flash cookie
  instead of the query string.* It would be one more cookie, plus a CSRF-style read-once dance,
  just to hide two words from the URL. The admin mappings page already uses the query string
  (`rebuilt`, `failed`).
- **A broker-file import names its importer explicitly; it isn't inferred.** `portfolio` has
  two import routes, one per parser registry: a trades export (`POST /transactions/import`) and
  an account statement / cash ledger, which needs live FX lookups while parsing
  (`POST /cashflows/import`). A broker can have either or both: one has both today, so its
  kind can't be inferred from the account. Guessing from the file would mean a new
  "can you parse this?" method on the plugin Protocols. Instead the import page lists the kinds
  `GET /brokers` reports for the account's own broker, and the visitor picks one. Another broker's
  importers are never offered. Every transaction in an account then comes from that account's
  broker, which broker-scoped ticker mappings rely on, and which `portfolio` enforces anyway by
  looking the parser up from the account's `broker` column. Before this, the page only ever
  called the transactions route, so a cashflow-only broker could only be imported through the
  `portfolio` CLI.
- **The account's broker is picked from a list, with "Custom" for anything else.** It used to
  be a free-text field with a `<datalist>` hint. A broker typed in a different case from its
  installed key (`Acme` vs `acme`) got no importer, and only failed later, at import. Now
  the field is a `<select>` of installed brokers, plus "Custom" with a name field. A custom
  broker is for manual entry only: its import page says so instead of showing a form. A custom
  name equal to an installed broker ignoring case is refused, since it would recreate that same
  trap. If brokers can't be listed it's let through, rather than blocking account creation on an
  outage. The name field is hidden by a CSS `:has()` rule unless "Custom" is chosen: no JS is
  needed, and a browser without `:has()` just always shows it. `portfolio` itself still accepts
  any broker string (its CLI and manual entry must work with no plugin installed). The list is a
  `web` form constraint, not a data rule, and existing accounts are untouched.
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
- **`GET /portfolio` is per-account, read-only, and never triggers a rebuild.** Every `portfolio`
  positions/returns endpoint is `account_label`-scoped with no cross-account aggregation endpoint,
  so the view is one page per account (linked from `accounts.html`); a cross-account "whole
  portfolio" rollup is future work. The route only displays whatever `position_snapshots`/
  `return_snapshots` rows already exist. `web` never calls a rebuild itself: `portfolio` rebuilds
  an account's positions and returns on every transaction write (see `portfolio/docs/flows.md`,
  "Positions and returns follow every transaction write"), and refreshes returns on a schedule
  ("Returns follow new prices"). An
  `AccountNotFound` from any of the positions, returns or latest-returns calls is treated as fatal
  (a distinct 404, "that account doesn't exist") rather than folded into the generic
  degrade-to-inline-error path `_list_accounts_and_brokers` (task 11) established, since a bad
  `account_label` has nothing partial to show either way; any other `PortfolioClientError`
  degrades only the part of the page that call feeds (holdings, chart, or tiles and table figures),
  mirroring `_list_accounts_and_brokers` exactly. An account with no
  positions and/or no return history yet renders as an ordinary empty state, not an error. Since
  UI-7 the page also says why figures are missing (see the next-but-one entry).
- **The overview's totals (UI-11) sum each account's own summary.** `GET /` fetches the
  account list, then one `GET /returns/latest` per account, concurrently. Each account is reduced
  with `summarize_latest_returns`, and `portfolio_view.summarize_accounts` adds those summaries
  up, recomputing the percentage from the summed euros. Running `aggregate_portfolio_returns`
  over every account's rows at once would be wrong: it groups by ticker alone, so one ticker held
  in two accounts would merge into one series and only one account's row would count. Each
  account's call degrades on its own, `AccountNotFound` included (an account deleted since it was
  listed). A failed account is left out of the totals, and a notice says how many were. That row
  reads "Couldn't load figures", never "Waiting for prices", which is for an account with no
  return row yet. A failed account list shows a notice, never the first-run state. The route
  lives in its own `api/overview.py` because `routes.py` is near the 64KB file limit.
- **The summary tiles and holdings figures (UI-6) come from `GET /returns/latest`, not from the
  chart's `GET /returns` history.** The latest row per ticker is also the history's last point,
  so on this page the call is redundant: one extra concurrent request, and during a rebuild the
  tiles can briefly disagree with the chart's last point. It's taken anyway because UI-11's
  cross-account overview needs a cheap "current figures" read per account, and fetching every
  account's full history for that would not be cheap. Built and tested here, the method is ready
  for UI-11. `portfolio_view.summarize_latest_returns` is `aggregate_portfolio_returns`'s last point
  over those rows. Forward-fill makes every ticker contribute its own latest row, including a
  ticker whose prices stopped earlier, and the point's date is the newest one: the page's "Prices
  as of" date. Reusing it keeps one rule for the percentage (recomputed, never averaged). A closed
  ticker keeps a quantity-0 latest row (its returns run through its closing date), so its
  dividends stay in the account's total return, matching the chart. `build_holdings` joins each
  open position to its latest row by ticker. Quantity and average cost come from the position.
  Market value and unrealized return come from the row. Weight is a share of the summed market
  value of the holdings that *have* a row. A holding with no row yet is "waiting for prices"
  (UI-7, next entry) rather than counting as worth zero, and a closed ticker's
  leftover row isn't a holding at all. Weights keep full precision, so displayed weights needn't
  sum to exactly 100%.
- **What's still pending is said on the page, not left blank (UI-7).** A missing figure has two
  possible causes, and they surface in different places because of where the data stops:
  - *Waiting for prices.* There is a position but no return row yet: data-store hasn't priced
    the ticker, or the scheduled refresh hasn't run since. The holdings row shows "Waiting for
    prices" across its four figure columns. This only shows when `GET /returns/latest` answered.
    If that call failed, the row keeps its `—`s, because prices may well exist.
    `_PortfolioViewData.failed` records which calls degraded, so an outage's empty list is never
    read as "none yet". *Not split* into "data-store doesn't track this ticker" versus "not priced
    yet" (user's decision). That would cost one more call (`list_tickers`) for a case an admin
    fixes upstream anyway.

    **Ticker onboarding (TK-9) splits out the one case a user causes:** a ticker they added that
    data-store is still loading, or gave up on.
    - For holdings with no return row only, the page asks `portfolio`'s
      `GET /instruments/tracked-tickers?ticker=…` for their status. That's one extra call, made
      only when such a holding exists.
    - The row reads "Prices loading" (`pending`/`backfilling`), "Prices couldn't be loaded"
      (`failed`), or still "Waiting for prices" for anything else.
    - The decision above stands for those other cases. A failed status call leaves every such
      row at "Waiting for prices", which is true either way (`ticker_status.load_statuses`).
  - *Waiting for a ticker mapping.* An unmapped instrument never becomes a position, because
    `portfolio`'s positions rebuild drops it before it has a ticker. So it can't be a holdings
    row, and is shown as a notice under the Holdings heading instead. The notice shows even with
    zero holdings, which is exactly the case that used to look like a bug. It comes from
    `portfolio`'s per-user `GET /instruments/unmapped`, fetched as the page's fourth concurrent
    call and filtered by `portfolio_view.unmapped_for_broker` to the account's broker. Admins get
    a link to `/admin/mappings`; everyone else is told an admin needs to map them.
    **Known sharp edge:** that list says which *broker* an instrument came through, not which
    account. Two accounts at the same broker therefore each show the other's unmapped
    instruments. The fix is a per-account unmapped endpoint in `portfolio`, deferred because
    this UI chunk adds no `portfolio` endpoints. When the account header degraded and the broker
    is unknown, no notice is shown rather than a guess.
  - A plain freshness line ("Prices update daily; returns are recalculated a few times a day.")
    sits next to "Prices as of" and in the no-history empty state. It names no interval because
    the refresh period is `portfolio`'s config (`PORTFOLIO_RETURNS_REFRESH_SECONDS`), not
    `web`'s.
- **The transactions list (UI-8) is the whole log, reversed in `web`.** `portfolio`'s
  `GET /transactions` returns an account's log oldest first (its replay order) and takes no
  paging, filter or sort parameters, so `web` fetches all of it and reverses it to show newest
  first, rather than adding a parameter to `portfolio` (this UI chunk adds no `portfolio`
  endpoints). **Known limitation:** a long-lived account renders every transaction it has ever
  had in one table. The fix, server-side pagination, is listed under Deferred in `tasks.md`.
  A failed call renders a banner and no table, never the "No transactions yet" empty state,
  because an outage must not read as an empty account. The type is a neutral badge (the
  charter's ink tag), never gain/loss colored, because a SELL or a WITHDRAWAL isn't a loss.
  Native figures (price, amount, fees) go through `native_money`. The frozen `eur_amount` is a
  muted second line under a non-EUR amount, rather than its own column: with a ninth column the
  table overflowed the 880px card at desktop width. A long reference is cut off with an ellipsis
  by CSS, its full value still in the page and in a `title` tooltip.

## Endpoint surface

| Route | Behaviour |
|---|---|
| `GET /` | `require_user` dependency (no valid session → redirected to `/login`, an `AuthRequired` exception mapped to a 303 by an app-level handler); the nav's "Overview" (UI-11, `api/overview.py`). Fetches `portfolio`'s `GET /accounts`, then `GET /returns/latest` per account concurrently, and renders a "Prices as of" line, the same four tiles as `GET /portfolio` totalled across accounts, and one row per account (label linking to its holdings, broker, market value, total return € and %). A row whose call failed says so and is left out of the totals, with a notice; an account with no return row yet is "Waiting for prices". No accounts → a first-run card linking to `/accounts`; a failed account list → a notice, never the first-run card (see "Key decisions") |
| `GET /admin` | `require_admin` dependency: an admin → the admin page (a link to ticker mappings), linked from the nav only for admins; a logged-in non-admin → a 403 page (`AdminRequired`, mapped by an app-level handler); no valid session → redirected to `/login`, like every `require_user` route |
| `GET /admin/mappings` | `require_admin`; fetches `portfolio`'s `GET /ticker-mappings`, `GET /instruments/unmapped/all` and `GET /instruments/tickers`, and renders three things: the instruments still waiting for a mapping (each with an inline "map to ticker" form), the existing mappings (each with a delete button), and a form to add or re-point one. Each list degrades on its own to a notice rather than an empty table, so an outage never reads as "nothing to map". After a save or delete, the redirect's query string (`saved`/`deleted`, `rebuilt`, `failed`) drives a one-line outcome banner |
| `POST /admin/mappings` | `require_admin`, CSRF-checked; calls `portfolio`'s `PUT /ticker-mappings` with the admin's own `user_id` as `updated_by` (a blank broker is sent as `null`, which an ISIN needs; portfolio enforces which kind needs one and its 422 message is shown as-is). Redirects to `/admin/mappings?saved=1&rebuilt=<n>&failed=<m>` |
| `GET /tickers` | `require_user`; ticker onboarding (TK-8, `api/tickers.py`). Lists `portfolio`'s `GET /instruments/tracked-tickers`, narrowed to `requested_by=<user>` for a user and unfiltered for an admin, with each ticker's status as a neutral badge (Queued, Loading, Ready, Couldn't load). An admin also sees each one's source (default list or a user) and its last error, with Retry for a failed ticker and Disable/Enable for a user-added one; a default-list ticker is turned on and off in `tracked_tickers.yaml`, so it gets no such control. A failed list call shows a notice, never "none added". `added`, `already` or `changed`+`change` in the query string drive the outcome banner, and only a ticker-shaped value is echoed |
| `GET /tickers/new` | `require_user`. A GET lookup form (`symbol`): a lookup changes nothing, works without JS, and can be bookmarked. With a symbol, it calls `GET /instruments/tickers/{t}/lookup` and shows what adding would do: for an `available` symbol, its name, exchange and currency plus an "Add" confirmation; otherwise where it already stands (`tracked`) or why it can't be added (`unsupported`, `not_found`, `unavailable`). A symbol quoted in minor units (`GBp`, `ZAc`, `ILA`) carries a warning that its values will read 100 times too high, the known limitation in `tasks.md` |
| `POST /tickers` | `require_user`, CSRF-checked; calls `POST /instruments/tracked-tickers` with the user's id and `enforce_limit = not user.is_admin`. This is the one place web decides who skips the daily limit. Redirects to `/tickers?added=<t>` or `?already=<t>`. Each refusal re-renders the add page with its own sentence: not on Yahoo (404), can't be added (422), daily limit reached (429), Yahoo didn't answer (503) |
| `POST /tickers/{ticker}/{retry,disable,enable}` | `require_admin`, CSRF-checked; calls `PATCH /instruments/tracked-tickers/{t}`. POST because a plain form can't send PATCH. A 404 or 409 (a default-list ticker's on/off, or retrying one that hasn't failed) re-renders the list with the reason |
| `POST /admin/mappings/{id}/delete` | `require_admin`, CSRF-checked; calls `DELETE /ticker-mappings/{id}`. POST rather than DELETE because a plain HTML form can't send DELETE and the page has no script. An already-deleted mapping (portfolio's 404, mapped to `TickerMappingNotFound` on this route only) re-renders with "That mapping was already deleted." |
| `GET /login`, `GET /signup` | `optional_user` dependency: already logged in → redirected to `/`; otherwise renders the form with a fresh or existing CSRF cookie |
| `POST /login`, `POST /signup` | CSRF-checked, calls `auth`, proxies the `Set-Cookie` `auth` returned, then redirects to `/` — a 303 for a plain form post, or an `HX-Redirect` response header for an `hx-post` (htmx's own XHR follows a 303 by swapping the *content* of the request's own response into the target element, which would drop a full HTML document into a form's error slot; `HX-Redirect` instead triggers a real browser navigation) |
| `POST /theme` | No login needed. CSRF-checked: a mismatch changes nothing and redirects back (an `HX-Redirect` for htmx), like `POST /logout`, rather than re-rendering a form error. A value other than `light`/`dark`/`system` → `400`. Otherwise sets the `stakehold_theme` cookie (`HttpOnly`, `SameSite=Lax`, `Path=/`, one year, `Secure` per `WEB_COOKIE_SECURE`), then a 303 to the validated `next`, or for htmx a `200` with `HX-Trigger`. See "Theme" in [`ui-architecture.md`](ui-architecture.md) |
| `POST /logout` | CSRF-checked, calls `auth` (best-effort — a failure is logged, not surfaced, since the user's intent to log out should not depend on `auth` being reachable), clears the session cookie locally, redirects to `/login` |
| `GET /health` | `200 {"status": "ok"}`, unauthenticated, no dependency check (see "Key decisions") |
| `GET /accounts` | `require_user`; lists the caller's accounts (`portfolio`'s `GET /accounts`) and installed brokers (`GET /brokers`) and renders them as a list (the name links to holdings; Transactions / Add / Import beside it), with the create-account form in a `<details>` below it, open on first run or after a create error (see [`ui-architecture.md`](ui-architecture.md)). Its broker field is a `<select>` of those brokers plus "Custom". A failed list shows a notice above the list, never the first-run steps |
| `POST /accounts` | CSRF-checked. With "Custom" chosen (`broker=__custom__`), sends the typed `custom_broker` name instead, after refusing a blank one or one matching an installed broker ignoring case (both 422, no `portfolio` write). Calls `portfolio`'s `POST /accounts`, redirects to `/accounts` on success |
| `GET /transactions/new` | `require_user`; required `account` query param (a bare 422 for a missing/blank value — reachable only via a hand-crafted URL, since every link that points here already includes it); renders the manual-entry form, with a confirmation banner if `?saved=<n>` (plus the rebuild outcome from `positions`/`returns`; see "Key decisions"). Fields are grouped into What / When / Amount / Instrument fieldsets; the reference is prefilled with a fresh `manual-<uuid4>`, the date with now (UTC) and the currency with EUR (a datalist suggests others), and `transaction_form.js` hides the fields the chosen type doesn't use (see [`ui-architecture.md`](ui-architecture.md)). The instrument field is a ticker picker: a native `<datalist>` filled from `portfolio`'s `GET /instruments/tickers` (no script, so the strict CSP is untouched). If that call fails, the form still renders with an empty list and a notice; the picker is a convenience, and portfolio checks whatever is typed when it's saved. Also fetches `GET /accounts` (concurrently) for the account header: a label that isn't one of the visitor's accounts renders `account_not_found.html` with a 404, and a failed fetch degrades to a label-only header rather than a 404 |
| `POST /transactions/new` | CSRF-checked, calls `portfolio`'s `POST /transactions` (an instrument, when given, is always sent as `instrument_identifier_kind = 'TICKER'`; a blank Amount is sent as `null`, for portfolio to derive on a BUY/SELL or reject as missing on anything else), redirects to `/transactions/new?account=<label>&saved=<transactions_saved>&positions=<status>&returns=<status>` on success. An unknown ticker comes back as portfolio's 422, whose message is shown as-is like every other 422. The ticker list is re-fetched for the error re-render |
| `GET /transactions/import` | `require_user`; required `account` query param; renders the upload form with an importer `<select>` listing the account's own broker's importers and the file input styled as a drop zone, with the 20 MB limit stated. With `?imported=<n>` it adds an import summary (count and rebuild outcome) and links to the account's holdings and transactions, with the form below as "Import another file". A broker with no importer gets a pointer to manual entry instead of a form; brokers that can't be listed get an inline notice, never the "no importer" text. Fetches `GET /accounts` (for the account header, with the same 404 and degrade rules as `GET /transactions/new`) and `GET /brokers` concurrently |
| `POST /transactions/import` | CSRF-checked, multipart; `importer` must be `transactions` or `cashflow` (anything else is a 422 with no `portfolio` call); size-capped at 20MB (matching `portfolio`'s own cap) before the bytes are forwarded; calls `portfolio`'s `POST /transactions/import` or `POST /cashflows/import` to match, redirects to `/transactions/import?account=<label>&imported=<rows_saved>&positions=<status>&returns=<status>` on success |
| `GET /transactions` | `require_user`; required `account` query param; fetches `portfolio`'s `GET /transactions` and `GET /accounts` (for the account header) concurrently, and renders the log newest first (reversed in `web`; see "Key decisions"): date, type badge, instrument, quantity, price, amount (with the EUR amount under it on a non-EUR row), fees, reference, and a Delete link (see `GET /transactions/delete`). No pagination. An empty log shows links to Add and Import. After a delete, `?deleted=1` (plus the rebuild statuses) shows "Transaction deleted." with the rebuild outcome, and `?deleted=0` says nothing was deleted because the row was already gone. `AccountNotFound`, or a label missing from a successfully loaded account list, renders `account_not_found.html` with a 404; any other failure renders a banner and no table, never the empty state; a failed account list degrades to a label-only header |
| `GET /transactions/delete` | `require_user`; required `account` and `ref` query params (`ref` is the row's `broker_transaction_ref`, which with the account is what identifies a transaction to `portfolio`). The no-JS confirmation page each Delete link on the log points to (`api/deletion.py`): the transaction's date, type, instrument, amount and reference, a "can't be undone" warning, and a form posting to `POST /transactions/delete`. `portfolio` has no single-transaction read, so the account's log is fetched (with `GET /accounts` for the header, concurrently) and searched. An unknown account is a 404 like the log's; a row that's gone redirects to the log with `deleted=0`; a failed fetch shows a notice and no form, since the visitor would be confirming a delete they can't see. With JS, `confirm_dialog.js` opens the same confirmation as a dialog on the log instead (see [`ui-architecture.md`](ui-architecture.md)) |
| `POST /transactions/delete` | `require_user`, CSRF-checked; posted by both the dialog and the page. Calls `portfolio`'s `DELETE /transactions` with the visitor's own `user_id`, then redirects to `/transactions?account=<label>&deleted=1&positions=<status>&returns=<status>`. POST rather than DELETE because a plain HTML form can't send DELETE. A 404 (the account or the row is gone; `TransactionNotFound`) redirects with `deleted=0` rather than showing an error: the visitor wanted the row gone. Any other failure, a CSRF mismatch included, re-renders the confirmation page with the error, so a delete started from the dialog can be retried from there |
| `GET /portfolio` | `require_user`; required `account` query param; fetches `portfolio`'s `GET /positions`, `GET /returns`, `GET /returns/latest` and `GET /instruments/unmapped` concurrently. Renders a "Prices as of" line and summary tiles (market value, cost basis, total return € and %, dividends; `portfolio_view.summarize_latest_returns`), a holdings table of open positions joined to their latest return row (quantity, average cost, market value, weight, unrealized return; `portfolio_view.build_holdings`), and a Chart.js chart of the per-ticker series aggregated into one portfolio-level series (`portfolio_view.aggregate_portfolio_returns`), with a total return / market value toggle and date ranges. `GET /accounts` is fetched concurrently for the account header. `AccountNotFound` on any call, or a label missing from a successfully loaded account list, renders `account_not_found.html` with a 404; any other failure degrades only the part that call feeds, mirroring `GET /accounts`'s own pattern. Pending states: "Waiting for prices" on a holding with no return row, and a notice listing the instruments waiting for a ticker mapping at this account's broker (UI-7). No writes, no rebuild trigger. |

Every POST error (wrong credentials, a duplicate email, a tripped throttle, `auth`/`portfolio`
being unreachable, a CSRF mismatch, or a `portfolio` validation failure) re-renders the form with
a flat, specific message — chapter IV of the brand charter's "no panic, no hype" applies to error
copy as much as to figures. A wrong password and an unknown email deliberately render the same
message, so `web` doesn't reintroduce the enumeration oracle `auth`'s own `passwords.dummy_verify()`
exists to close; no equivalent concern applies to the portfolio routes; see "Key decisions" above.

Any path no route matches is a branded 404 page, any other HTTP error or malformed request a
branded page with its own status, and an unhandled exception a branded 500 (UI-12; see
"Feedback and error states" in [`ui-architecture.md`](ui-architecture.md)). None of them is JSON.

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
- `portfolio_client/{base,http_client,errors}.py` (task 11, extended tasks 12 and 17) — the
  `PortfolioClient` Protocol, its real implementation, and one error hierarchy
  (`PortfolioClientError`); see "Key decisions" for how and why it departs from `auth_client`'s
  shape in a couple of places. Task 12 added `list_positions`/`list_returns` (plus `Position`/
  `Return` dataclasses) to the same trio rather than inventing new files — no new error types were
  needed, since `AccountNotFound`/`PortfolioUnavailable` already cover every failure mode these
  two reads can produce. Task 17 added the ticker-mapping calls (`TickerMapping`,
  `UnmappedInstrument`, `TickerMappingWriteResult`) and one error, `TickerMappingNotFound`: a 404
  from the mapping delete means the mapping is gone, not an account, so it can't reuse
  `AccountNotFound`. UI-6 added `list_latest_returns` (`GET /returns/latest`), parsed into the
  same `Return` dataclass, since `portfolio` serves it with the same response model. UI-7 added
  `positions_rebuild`/`returns_rebuild` to `TransactionWriteResult`. They are `None` when an
  older response lacks them, so a version skew degrades the banner rather than failing the
  write. UI-7 also added `list_unmapped_instruments` (the per-user `GET /instruments/unmapped`),
  which reuses `UnmappedInstrument`. UI-14 changed `list_brokers` to return `Broker`
  dataclasses (a key plus its import kinds) and added `import_cashflow`, which shares
  `import_transactions`' multipart upload helper. Unlike UI-7's optional fields, a `/brokers`
  response in the old bare-string shape is raised as `PortfolioUnavailable` rather than
  degraded. Guessing "no imports" would tell the visitor their broker has no importer when it
  may have several. UI-8 added `list_transactions` (`GET /transactions`) and its
  `Transaction` dataclass, mirroring `portfolio`'s `TransactionResponse`. It returns
  `portfolio`'s order unchanged; the route reverses it. The transaction delete added
  `delete_transaction` (`DELETE /transactions`), its `TransactionDeleteResult` (the rebuild
  statuses as on `TransactionWriteResult`), and `TransactionNotFound`. `portfolio` 404s an account
  that doesn't resolve and a reference that isn't in it alike, and either way there is nothing of
  the visitor's to delete, so the client maps every 404 on this call to that one error rather than
  guessing from the message.
- `rebuild_outcome.py` (UI-7) — pure. Turns the two rebuild statuses into the confirmation
  banner's one sentence; see "Key decisions".
- `portfolio_view.py` (task 12) — pure, I/O-free reshaping of what `routes.py` fetches for
  `GET /portfolio`: `filter_open_positions`, `aggregate_portfolio_returns`, and (UI-6)
  `summarize_latest_returns` and `build_holdings` with its `HoldingRow`, and (UI-7)
  `unmapped_for_broker` (see "Key decisions"), and (UI-11) `summarize_accounts` with its
  `AccountOverview`, for `GET /`. No FastAPI/httpx dependency, tested in isolation, mirroring how `portfolio`'s own
  `returns/calculator.py` is a pure function.
- `formatting.py` (UI-5) — pure display formatters (`money`, `signed_money`, `qty`, `pct`,
  `date`, UI-6's `share` and `delta_class`, and UI-8's `native_money`) plus the `FILTERS` mapping `app.py` registers as Jinja filters; see [`ui-architecture.md`](ui-architecture.md).
- `api/overview.py` (UI-11) — `GET /`, on its own router (like `api/theme.py`) because
  `routes.py` is near the 64KB file limit. It renders through `routes._render`, so the
  `_finish` header merge stays in one place.
- `api/tickers.py` (TK-8) — the Tickers pages, on their own router for the same file-size
  reason, rendering through `routes.py`'s helpers so CSRF and cookie handling stay identical.
- `ticker_status.py` (TK-8/TK-9) — how a tracked ticker's status reads (labels, a holding's
  price note) and the degrading status lookup the portfolio page uses. Its own module so
  `routes.py` and `api/tickers.py` share it without importing each other.
- `api/deletion.py` — the transaction delete: `GET`/`POST /transactions/delete`, on its own
  router for the same reason as `api/overview.py`, rendering, redirecting and checking CSRF
  through `routes.py`'s helpers.
- `api/theme.py` (UI-3) — the theme cookie, `POST /theme` on its own router, the
  `theme_context` context processor, and `safe_next`. It's a separate module because it's
  self-contained and needs neither upstream client.
- `api/{app,deps,routes,csrf,security_headers,htmx}.py` — app assembly and lifespan (opens/closes
  two shared `httpx.AsyncClient`s, one per upstream service), FastAPI dependencies (`require_user`,
  `require_admin`, `optional_user`, `client_ip`, `get_auth_client`/`get_portfolio_client`, and friends), the route
  handlers, the CSRF helpers, the security-headers middleware, and `htmx.py`'s
  `is_htmx`/`htmx_or_redirect` (shared by `routes.py`'s redirects and `app.py`'s `AuthRequired`
  handler, so every redirect in the app picks htmx-partial vs. full-navigation the same way).
- `templates/`, `static/` — Jinja2 templates (`base.html`, which holds the skip link, the
  top nav and the theme toggle, plus one per page; `partials/_form_errors.html` for the htmx swap
  target; `partials/_banner.html`, the message macro, and `partials/_logo.html`, the two-logo
  pair (UI-12); `partials/_account_header.html`, included by the four account-scoped pages;
  `partials/_delta.html`, the gain/loss-colored figure macro shared by home and portfolio;
  `partials/_delete_dialog.html`, the confirmation dialog macro, and
  `partials/_transaction_summary.html`, the one-line transaction description the dialog and
  `transaction_delete.html` both show;
  `account_not_found.html`, their shared 404; and `error.html`, every other error page) and
  `web.css` plus vendored `htmx.min.js`. Same-origin scripts: `htmx-error-swap.js` (also the
  generic failure banner, UI-12), `portfolio_chart.js`, `theme.js` (UI-3, applies a theme
  switch in place), `transaction_form.js` (UI-9, shows the manual-entry fields the chosen type
  and currency need), `password_toggle.js` (UI-12), and `confirm_dialog.js` (opens a delete's
  confirmation as a dialog in place of its page).
- `cli.py` — `web serve` only, mirroring `auth serve`'s lazy-import-uvicorn shape.
- `scripts/review_pages.py` (UI-13) — a developer tool, not part of the package and not an
  entry point of the service. It screenshots every page against fake clients and checks
  contrast, focus and phone-width overflow; see [`ui-architecture.md`](ui-architecture.md).
  Playwright comes from the workspace `dev` group.

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
  transaction deletes, and (task 12) reading positions/returns, server-to-server, carrying
  `WEB_PORTFOLIO_INTERNAL_SECRET` as `X-Internal-Secret`. Rebuild triggers aren't called from
  `web`: `portfolio` rebuilds positions and returns itself on each transaction write, and
  refreshes returns on a schedule. `web` only reports the write's rebuild statuses (UI-7).
  Ticker onboarding (TK-8) goes through `portfolio` too: its `/instruments/tickers/{t}/lookup`
  and `/instruments/tracked-tickers` routes pass straight through to `data-store`.
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
`portfolio/docs/flows.md`'s own "HTTP API" section).

Closed by task 12: the charting library choice (Chart.js, vendored); the portfolio-level return
aggregation design (`portfolio_view.aggregate_portfolio_returns`); confirmed no direct `data-store`
call is needed for this view.
