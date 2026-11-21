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
this file for size, `/labs`'s in [`labs-ui.md`](labs-ui.md) and `/risk`'s in
[`risk-ui.md`](risk-ui.md), each split out of that one. All three count as part of this document
under `.claude/rules/architecture.md`.

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
- **Asset URLs are versioned by content (`static_assets.py`).** Templates never write a
  `/static/...` or `/brand/...` URL by hand; they call the `asset_url` Jinja global, which
  appends `?v=` and the first 12 hex digits of the file's SHA-256 (StaticFiles ignores the
  query). Why: `StaticFiles` sends no `Cache-Control`, so a browser judges a cached file's
  freshness on its own, from its `Last-Modified`, and can reuse it for days without asking.
  With fixed URLs, a browser kept a stale `chart_theme.js` after `formatPct` moved into it, and
  the new drawdown chart threw and drew nothing. Changing a file now changes its URL, and an
  unchanged file keeps its URL and its cache. A hash is recomputed only when the file's mtime or
  size changes, so a page costs a `stat` per asset, and an edited mounted `brand/` file gets a
  new URL without a restart. An unknown path raises `UnknownAsset` at render instead of
  shipping a broken link. Rejected: `Cache-Control: no-cache`, which is simpler but costs a
  revalidation round trip per asset on every page load. Known gap: URLs inside a stylesheet
  (`tokens.css`'s Google Fonts `@import`) aren't versioned. None of them points at our own
  files today. A unit test fails if a template links an asset without `asset_url`.
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
- **No growth since inception in the portfolio views.** A time-weighted growth figure (from
  `portfolio`'s `GET /returns/growth` and `GET /returns/portfolio`) used to show as a tile on
  `/accounts/holdings` and `/portfolio`, a Growth % column on `/portfolio`, and a chart view on
  all three chart pages. It was removed at the user's request: that kind of performance analysis
  belongs to a dedicated backtesting module, not yet built. The "Total return" tile's % (return
  over current cost basis) is the only percentage the views show.
- **`GET /accounts/holdings` is per-account, read-only, and never triggers a rebuild.** It reads
  `portfolio`'s `account_label`-scoped positions/returns endpoints, so the view is one page per
  account (linked from `accounts.html` and the home overview). The page across accounts is
  `/portfolio` (PF-4, below). The route only displays whatever `position_snapshots`/
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
- **The old `/portfolio?account=X` URL answers a 308 to `/accounts/holdings?account=X` (PF-2).**
  The per-account page moved so `/portfolio` could be the combined page across accounts;
  bookmarks still carry the old URL, so it redirects rather than 404s. Permanent, because a
  browser caches it per URL, query included, so the bare `/portfolio` is never mistaken for it;
  308 because it is the code the agreed requirement names and it keeps the method. The query
  string goes on verbatim, so an encoded label stays encoded, and the path is fixed, so it can't
  be turned into an open redirect. Templates link to `/accounts/holdings` directly (a unit test
  checks no template contains `/portfolio?`).
- **`GET /portfolio` is the combined page, and the redirect still asks nobody who is logged in
  (PF-4).** One handler in `api/portfolio.py` branches on `account`. The redirect reveals nothing
  and its target requires login, so it costs no `auth` call; a `Depends(require_user)` would run
  before the handler could branch, so the handler calls `require_user` itself after the redirect
  check. **Sharp edge:** overriding `require_user` in `dependency_overrides` doesn't reach this
  page; tests override `get_auth_client` instead (`test_portfolio_flow.py`).
  The page sums nothing itself. portfolio's `GET /holdings` (PF-1) does, so it is one call
  however many accounts there are, against the overview's one per account. It also calls
  `GET /accounts` and `GET /instruments/unmapped`, concurrently, each degrading on its own: the
  account list only tells "no accounts" (the first-run card) from "accounts holding nothing"
  (`/holdings` answers both with an empty list), and a failed list is never read as "no
  accounts". A failed `/holdings` shows a notice in place of the table and no tiles, never an
  empty table. The tiles come from `/holdings`' `totals`, not from the rows: totals count closed
  positions' dividends, the home page's rule, so the two pages agree. A holding is unpriced
  unless every account holding it has a return row (portfolio's rule, so no partial sum is
  shown); unpriced holdings say where their prices stand (`ticker_status.statuses_from`), read
  from the same tracked-ticker call that names every holding (UX-2).
  PF-6 adds a fourth concurrent call, `GET /returns/portfolio` (PF-5). It alone feeds the chart,
  so a failure shows a notice in the chart card and nothing else. The chart's island is portfolio's series as served
  (`build_portfolio_chart_payload`), never web's own aggregation, which groups by ticker alone.
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
    - The page asks `portfolio`'s `GET /instruments/tracked-tickers` once, unfiltered, when it
      has any holding (UX-2). The same rows give each ticker's name and, for holdings with no
      return row, their status, so naming the holdings added no second call.
    - The row reads "Prices loading" (`pending`/`backfilling`), "Prices couldn't be loaded"
      (`failed`), or still "Waiting for prices" for anything else.
    - The decision above stands for those other cases. A failed status call leaves every such
      row at "Waiting for prices", which is true either way (`ticker_names.load_tracked`).
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
  table overflowed the 880px card at desktop width. Since LY-2 the page takes the full content
  width and the reference shows whole; between phone and about 1150px the table scrolls in its
  own box (`ui-architecture.md`, "Page width"). A stacked row still cuts a long reference off
  with an ellipsis, its full value in a `title` tooltip.

## Endpoint surface

Every route `web` serves, with what it requires and which upstream calls it makes, is in
[`endpoints.md`](endpoints.md), split out of this file for size.

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
  (`PortfolioClientError`). The Labs and risk response parsers moved verbatim to
  `risk_parsing.py`, and the JSON primitives they share to `parsing.py`, when `http_client.py`
  neared 64 KB (RK-29): a split by subject, as portfolio's `risk_jobs.py`; see "Key decisions" for how and why it departs from `auth_client`'s
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
  guessing from the message. The account delete added `delete_account` (`DELETE /accounts`) and
  its `AccountDeleteResult`; its 404 is the existing `AccountNotFound`. (A `list_growth` call for
  `GET /returns/growth` existed until growth since inception was removed.) PF-3 added `list_holdings` (`GET /holdings`) and its
  `Holdings`/`Holding`/`HoldingAccount`/`HoldingsTotals`, which mirror PF-1's response field for
  field. The summing across accounts is `portfolio`'s, so the combined page makes one call
  rather than one per account. `priced` and `mixed_currency` must be real JSON booleans or the
  response is `PortfolioUnavailable`: a string `"false"` is truthy, and misreading `priced`
  would show a partial sum as the holding's value. No new error type was needed, since
  `/holdings` has no 404 (a user with no accounts gets an empty result). PF-6 added
  `list_portfolio_returns` (`GET /returns/portfolio`, the full series: the chart's ranges crop
  on the client) and its `PortfolioReturns`/`PortfolioSeriesPoint`; no 404
  either. LB-5 added `backtest` (`POST /labs/backtest`, read-only despite the POST) and its
  `Backtest`/`BacktestRow`/`BacktestPeriodReturn`/`BacktestGrowth`/`BacktestAssetStatus`, plus
  `BACKTEST_PERIODS` for the column order, since web can't import portfolio's `Period`. A 422 is
  `InvalidPortfolioRequest`. Every other status, a 404 included, is `PortfolioUnavailable`: the
  route has no account, so `_raise_for_status`'s 404-means-`AccountNotFound` would mislead. An
  asset portfolio can't backtest is a status in the result, never an error. Three checks go
  beyond typing, so that a broken contract can't render as wrong figures: every row must have all
  seven periods, every growth line one value per date, and every period and the growth must carry
  `left_out` (LB-10), since reading a missing one as "nothing left out" would pass the mix's
  figure off as every asset's. A value can be null: a Max line before its
  asset's first day (LB-9). The shared 10 s timeout fits: the flow took
  about 2.2 s for 50 assets over Max against the dev data. RK-12 added `ex_post_risk`
  (`GET /risk/ex-post`) and its `ExPostRisk`/`ExPostWindow`/`ExcludedHolding`, reusing RK-9's
  `RiskFigure`, `RISK_FIGURE_KEYS` and figure parser; `RISK_WINDOWS` names the standard windows
  both tables use (`BACKTEST_RISK_WINDOWS` is the same tuple). As for `backtest`, every non-200
  is `PortfolioUnavailable`. Beyond typing, every window must carry all twelve figures, none of
  them null (an uncovered ex-post window greys each figure, unlike a backtest window), and the
  windows must be exactly `RISK_WINDOWS` in order, so a figure can't land under the wrong
  heading. An empty `windows` list is the "nothing to measure" answer, not an error. RK-14
  made both calls take a `benchmark` (RK-13's parameter; `DEFAULT_BENCHMARK`, `VWCE.DE`, mirrors
  portfolio's default) and parse what portfolio measures against it: each window's `relative`
  figures (`RELATIVE_FIGURE_KEYS`, a dict like `figures`), a `BenchmarkStatus`, and on the
  backtest a `BacktestBenchmark` that adds the growth line. All are read as required keys, like
  `risk`. A relative figure is never null, though a backtest window's whole `relative` is when
  the window isn't covered. The benchmark's growth line must have one value per growth date and
  no null point (it is null as a whole, with `growth_reason`, never in part), and a growth line
  with no growth series is a contract break. RK-23 added `look_through` (`POST
  /risk/look-through`, RK-22) and its `LookThrough*` types. Its weights stay `Decimal` and must
  be fixed-point strings, as portfolio serves shares: a JSON number would mean the contract
  moved to floats. `coverage` and `dimensions` must carry exactly `LOOK_THROUGH_COVERAGE` and
  `LOOK_THROUGH_DIMENSIONS`, so a missing dimension can't silently drop a table and a new one
  can't silently go unshown. RK-20 added `what_if` (`POST /risk/what-if`, RK-19) and its
  `WhatIf*` types. `rows` must carry every `WHAT_IF_ROW_KEYS` key and each row an "after"
  figure, so a row can't go missing or show a blank. The flags (`capped`, `survivorship`,
  `shortened`, `improves`) must be real booleans, since a misread `survivorship` would drop
  R14's warning. The before/after weights aren't parsed: the page shows only the candidate's,
  and what isn't read can't break. RK-29 added `rank_candidates` (`POST /risk/what-if/ranking`)
  and `CandidateRanking`: each ranked row must carry exactly `RANKING_ROW_KEYS`, reusing the
  what-if's row parser.
- `rebuild_outcome.py` (UI-7) — pure. Turns the two rebuild statuses into the confirmation
  banner's one sentence; see "Key decisions".
- `portfolio_view.py` (task 12) — pure, I/O-free reshaping of what `routes.py` fetches for
  `GET /accounts/holdings`: `filter_open_positions`, `aggregate_portfolio_returns`, and (UI-6)
  `summarize_latest_returns` and `build_holdings` with its `HoldingRow`, and (UI-7)
  `unmapped_for_broker` (see "Key decisions"), and (UI-11) `summarize_accounts` with its
  `AccountOverview`, for `GET /`, and (PF-4) `build_portfolio_holdings` (weight over priced
  rows only) and `unmapped_across_brokers`, and (PF-6)
  `build_portfolio_chart_payload`, for `GET /portfolio`. No FastAPI/httpx dependency, tested in isolation, mirroring how `portfolio`'s own
  `returns/calculator.py` is a pure function.
- `formatting.py` (UI-5) — pure display formatters (`money`, `signed_money`, `qty`, `pct`,
  `date`, UI-6's `share` and `delta_class`, UI-8's `native_money`, RK-9's `pct1`,
  `signed_pct1` and `ratio` for risk figures, spec §10's one decimal and two, and RK-20's
  `signed_points1` and `signed_ratio` for the what-if's changes) plus the `FILTERS` mapping `app.py` registers as Jinja filters; see [`ui-architecture.md`](ui-architecture.md).
- `api/overview.py` (UI-11) — `GET /`, on its own router (like `api/theme.py`) because
  `routes.py` is near the 64KB file limit. It renders through `routes._render`, so the
  `_finish` header merge stays in one place.
- `api/holdings.py` — `GET /accounts/holdings`, one account's holdings, tiles and chart, on its
  own router for the same file-size reason. Moved out of `routes.py` unchanged when the (since removed) growth
  view was added (as `api/portfolio_page.py`, serving `/portfolio`; PF-2 renamed both); it still
  renders through `routes._render` and shares `routes.py`'s account-page header helpers with the
  transactions and import pages.
- `api/portfolio.py` — `GET /portfolio`: the combined page across accounts (PF-4) and the 308
  from the old per-account URL (PF-2), in one handler (see "Key decisions").
- `api/labs.py` (LB-6) — `GET /labs`, the Labs backtest page, on its own router for the
  same file-size reason as `api/overview.py`. Fetches holdings, then the backtest for the
  `?period=` the chart shows (LB-8), each failing on its own. LB-7 added the edited mix: the
  ticker list is fetched alongside the holdings, or alone for an edited mix, which needs no
  holdings. RK-9 added `?risk_window=`, the window the risk comparison shows. Every window is
  in the one backtest response, so it changes only the rendering, and the backtest's `risk`
  section gets one log line (end date, the common window's start and what limits it, windows
  covered, and the risk-free status). RK-14 added `?benchmark=` (`benchmark_param.py`), sent
  with the backtest. Unlike the window it reruns a different backtest, so its form re-sends the
  rest of the query as hidden inputs; the log line adds the benchmark, whether it is available,
  and whether it has a growth line.
- `labs_risk_view.py` (RK-9) — pure: how the backtest's `risk` section is shown. The mix's
  grid of measures by window (`build_mix_grid`), the mix and its assets over one window
  (`build_comparison`), and the drawdown chart's island (`build_drawdown_payload`). It sits
  apart from `labs_view.py` because it owns the wording of a statistical estimate (its format,
  its greyed reason, its flags' notes), where `labs_view.py` owns which holdings make the mix.
  An unknown flag shows its code and is logged, never dropped. RK-14 added `RELATIVE_MEASURES`,
  the four figures against the benchmark, as a group of grid rows and four comparison columns.
- `benchmark_param.py` (RK-14) — pure: `chosen_benchmark`, what `/labs` and `/risk` send for
  `?benchmark=`. Its own module because both routers share it and neither imports the other.
  It catches only what portfolio would reject (over 20 characters, a 422 for the whole
  backtest). An untracked ticker is still sent, because portfolio answers it with a 200 and a
  reason, so web keeps no second copy of the tracked list.
- `labs_mix.py` (LB-7) — pure: the edited mix's query-string contract (`parse_mix`,
  `canonical_query`) and its checks (`check_mix`). It sits apart from `labs_view.py` because it
  owns a different thing: what the user typed and whether it can be sent, where `labs_view.py`
  derives a mix from holdings. The query string is the whole state, since a lab is never
  stored.
- `labs_view.py` (LB-6) — pure: `build_default_mix`, the mix `/labs` sends when the visitor
  hasn't edited one. Reuses `portfolio_view.build_portfolio_holdings` for the weights, so
  `/labs` and `/portfolio` can't disagree about them, and mirrors `portfolio`'s 50-asset cap
  (`MAX_BACKTEST_ASSETS`) so a large portfolio is cut to its largest holdings, not rejected.
  LB-8 added `build_growth_payload`, the growth chart's data island, rounded to six
  significant digits. RK-14 added the benchmark's line to it, or the reason it has none.
- `api/risk.py` (RK-12) — the Risk pages, on their own router for the same file-size reason.
  UX-6 split the one page into four routes under one sub-nav: `/risk` (ex-post),
  `/risk/ex-ante`, `/risk/what-if` (the what-if and the ranking) and `/risk/concentration`.
  Each loads only its own sections, so a view no longer pays for all five; *rejected: one page
  with collapsible sections*, which still loads every section, *and JS tabs*, which need a
  separate no-JS fallback (see [`risk-ui.md`](risk-ui.md)). Each section has its own loader
  returning its data or the notice to show in its place, so one failing section never takes
  its page down. Every page but ex-post measures today's mix, so each starts with one
  `GET /holdings` (`_load_mix`, through `labs_view.build_default_mix`, so the weights are
  worked out in one place); a failed holdings call shows its notice in each of that page's
  sections. The what-if page's two loaders then run concurrently on that one mix. A stale or
  all-covering `?exclude=` is dropped in web and logged (`_sent_exclusions`), so an old link
  never becomes portfolio's 422. It is ex-ante's, and the what-if and the ranking resolve it
  the same way, so their "before" is the mix the ex-ante page shows; the look-through always
  gets the whole mix. The what-if form is checked first (`risk_what_if_view.parse_what_if`),
  and an empty form or one with errors makes no call: a 422 would show as a failed section,
  not as the field to fix. RK-29's ranking runs only when its form is sent: it costs two
  data-store calls per tracked ticker. RK-14 added `?benchmark=` and the ticker list for its
  field, fetched concurrently with the figures and failing on its own, as on `/labs`. The
  sub-nav carries the whole query (`_with_query`), so each page's controls survive a trip to
  another, as they did on one page.
- `risk_view.py` (RK-12) — pure: `build_ex_post_table`, the ex-post summary table (measures as
  rows, windows as columns) with each figure's greyed reason, its metadata `title` and its flag
  markers, plus the legend of the flags present. The template only lays it out. RK-14 added
  the relative rows, built the same way, and the benchmark they are measured against. RK-18
  made its cell, title and legend helpers public (`figure_cell`, `figure_title`, `legend_for`,
  `SUMMARY_ROWS`) for the ex-ante section, rather than keeping a second copy.
- `risk_ex_ante_view.py` (RK-18) — pure: `build_ex_ante_section`, the ex-ante section (the
  constant-weight summary, the decomposition and its insight note, the € tail figures, the
  heatmap's squares and buckets, RK-26's FX decomposition table, and the window, exclude and
  include links). Its own module because it has five parts of its own; it reuses `risk_view`'s cells so the two sections'
  figures read alike. See "Risk page" in [`risk-ui.md`](risk-ui.md). RK-23
  made the default mix's left-out wording public (`mix_left_out`) for the concentration section.
- `risk_concentration_view.py` (RK-23) — pure: `build_concentration_section`, the concentration
  section (the coverage split and the unmapped funds, the currency table's two views, sector,
  country and asset class, the top names, each table's "show all" split and bars, and what was
  left out). Its own module for the same reason as the ex-ante one; see "Risk page" in
  [`risk-ui.md`](risk-ui.md).
- `risk_regime_view.py` (RK-28) — pure: `build_regime_section`, a section's regime table
  (the regimes grouped by dimension, each with its own and its measured dates, the four cells
  and the short-regime legend, or why there are none). Ex-post and ex-ante each build one from
  their own response, so it isn't part of either section's module. The router carries it beside
  the rolling charts in `api/risk.py`'s `_Extras`. See "Risk page" in [`risk-ui.md`](risk-ui.md).
- `risk_rolling_view.py` (RK-25) — pure: `chosen_pair` (the rolling correlation's pair from
  `?corr_a=&corr_b=`, or the default), `build_rolling_charts` and `build_correlation_chart`
  (each chart's data island, its summary in words and its gap notes, and the pair picker). Its
  own module, not part of the ex-ante one, because ex-post draws the same charts. See "Risk
  page" in [`risk-ui.md`](risk-ui.md).
- `risk_what_if_view.py` (RK-20) — pure: `parse_what_if`, the form's checks and the request
  it makes, and `build_what_if_section`, the before/after table, the threshold and why there's
  nothing to compare. It reuses `risk_view`'s cells and legend, and `labs_mix.parse_weight`
  (public for it) so a weight is read one way on both pages. See "Risk page" in
  [`risk-ui.md`](risk-ui.md).
- `risk_ranking_view.py` (RK-29) — pure: `parse_ranking`, the form's weight, order and 1Y
  check, and `build_ranking_section`, the numbered rows, greyed and unranked candidates and the
  order links. It reuses `risk_view`'s cells and the what-if's change filters. See "Risk page"
  in [`risk-ui.md`](risk-ui.md).
- `api/tickers.py` (TK-8) — the Tickers pages, on their own router for the same file-size
  reason, rendering through `routes.py`'s helpers so CSRF and cookie handling stay identical.
- `ticker_status.py` (TK-8/TK-9) — how a tracked ticker's status reads (labels, a holding's
  price note), and `statuses_from`, a holding page's statuses from `ticker_names.load_tracked`'s
  rows. Its own module so `api/holdings.py` and `api/tickers.py` share it without importing each
  other.
- `ticker_names.py` (UX-1, UX-2) — `load_tracked`, every tracked ticker from one unfiltered
  `list_tracked_tickers` call, and `load_names`, those rows as the page's
  `{ticker: display_name}` for the `ticker` macro and the pickers. A failure returns `[]`/`{}`:
  a name is cosmetic, so the page shows bare tickers rather than an error.
  - *Unfiltered, not filtered to the page's tickers.* The pickers offer every priced ticker
    (~1.3k, mostly fund constituents), too many `ticker=` params for one query string, and only
    tracked tickers have a name: the YAML's handful plus user requests, capped by the daily
    limit. So one small call covers a page's pickers, tables and prose, and no route has to
    collect tickers from Risk's nested view models, where one would be easy to miss.
  - *Known cost:* the response grows with the tracked list. If that reaches thousands, ask for
    the page's own tickers again (and a separate call for the pickers).
- `static_assets.py` — `make_asset_url`, the content-versioned `/static` and `/brand` URLs
  behind the `asset_url` Jinja global that `app.py` registers (see "Asset URLs are versioned by
  content" above). It needs no upstream client.
- `api/deletion.py` — the deletes: `GET`/`POST /transactions/delete` and
  `GET`/`POST /accounts/delete`, on their own router for the same reason as `api/overview.py`,
  rendering, redirecting and checking CSRF through `routes.py`'s helpers.
- `transaction_counts.py` — `load_transaction_counts`, each account's transaction count for
  an account delete's warning. Its own module, like `ticker_status.py`, so `routes.py` (the
  accounts list) and `api/deletion.py` (the confirmation page) share it without importing each
  other.
- `api/glossary.py` — `GET /glossary`, on its own router for the same file-size reason as
  `api/overview.py`; it needs neither upstream client.
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
  `partials/_ticker.html` (UX-1), a ticker with its full name in a tooltip;
  `partials/_fold.html` (UX-7), the `<details>` that folds secondary detail;
  `partials/_delete_dialog.html`, the confirmation dialog macro, and
  `partials/_transaction_summary.html` and `partials/_account_summary.html`, the one line
  naming what a delete takes, which a dialog and its confirmation page
  (`transaction_delete.html`, `account_delete.html`) both show;
  `account_not_found.html`, their shared 404; and `error.html`, every other error page) and
  `web.css` plus vendored `htmx.min.js`. Same-origin scripts: `htmx-error-swap.js` (also the
  generic failure banner, UI-12), `chart_theme.js` (LB-8, the theme colors and redraw both charts share), `portfolio_chart.js`,
  `labs_chart.js` (LB-8, the Labs growth chart), `labs_drawdown_chart.js` (RK-9, the mix's
  drawdown under it; both take `formatPct` from `chart_theme.js`), `risk_rolling_chart.js`
  (RK-25, every Risk rolling chart, from its data island; one in a closed fold is drawn when
  the fold first opens, UX-7), `risk_anchor.js` (UX-6, sends an
  old `/risk#section` link on to its sub-page; a fragment never reaches the server), `theme.js` (UI-3, applies a theme
  switch in place), `ticker_tooltip.js` (UX-1, Escape closes a ticker's name tooltip), `account_menu.js` (UX-4, closes the header's account menu on Escape, an outside click or focus leaving it), `transaction_form.js` (UI-9, shows the manual-entry fields the chosen type
  and currency need), `password_toggle.js` (UI-12), `confirm_dialog.js` (opens a delete's
  confirmation as a dialog in place of its page), and `holdings_breakdown.js` (shows and hides a
  `/portfolio` holding's per-account breakdown from its ticker), and `labs_form.js` (LB-7, adds
  rows to the Labs mix form in place; UX-8, scrolls an added row into view in a long mix's box
  and makes that box a keyboard-scrollable region).
- `cli.py` — `web serve` only, mirroring `auth serve`'s lazy-import-uvicorn shape.
- `scripts/review_pages.py` (UI-13) — a developer tool, not part of the package and not an
  entry point of the service. It screenshots every page against fake clients and checks
  contrast, focus, sideways scroll, tables overflowing their cards and chart sizing, at phone,
  tablet, desktop and wide widths; see [`ui-architecture.md`](ui-architecture.md).
  Playwright comes from the workspace `dev` group. Its fake clients and their review data are
  `scripts/review_fakes.py` (split out in RK-23, when the script reached 62 KB).

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
  and `/instruments/tracked-tickers` routes pass straight through to `data-store`. So do Labs
  backtests (LB-5): `POST /labs/backtest` reads price and FX history from `data-store` on
  `portfolio`'s side, so `web` still never calls `data-store`. The same holds for the risk page
  (RK-12): `GET /risk/ex-post` fetches prices, FX and the risk-free rate on `portfolio`'s side.
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
- **Service-to-service trust**: resolved for now via network isolation plus a shared internal
  secret — see [`auth`'s architecture doc](../../auth/docs/architecture.md#key-decisions). The
  stronger fix (signed identity assertions from `auth`) is deliberately deferred; see that doc's
  "Open questions" for the trigger to revisit it.

Closed by task 10: deployment shape for `web`'s own container (see "Deployment" above). Closed by
task 13: the multi-service network topology. The exact `auth` API contract (see
[`endpoints.md`](endpoints.md) and `auth/docs/architecture.md`'s own "Endpoint surface").

Closed by task 11: the exact `portfolio` API contract (see [`endpoints.md`](endpoints.md) and
`portfolio/docs/flows.md`'s own "HTTP API" section).

Closed by task 12: the charting library choice (Chart.js, vendored); the portfolio-level return
aggregation design (`portfolio_view.aggregate_portfolio_returns`); confirmed no direct `data-store`
call is needed for this view.
