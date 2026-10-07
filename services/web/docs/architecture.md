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
    (see "Theme" under "Key decisions") are the only places in the file a brand *color* token may
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
- **Chart.js is vendored (`static/vendor/chart.min.js`), and the chart reads its colors from
  the semantic tokens at runtime rather than from a hardcoded hex value.** `script-src 'self'` (no CDN
  exception, no `unsafe-inline`, no nonce) rules out both a CDN `<script src>` and an inline
  `<script>` block with real logic — this codebase has never had one, and adding the first here
  would either be silently blocked by the browser or force a CSP weakening this task's own
  no-CDN framing argues against. The chart's numeric data is embedded via a non-executable
  `<script type="application/json">` data island in `portfolio.html` (inspected as data, not run
  as script — needs no `script-src` permission to be read); the logic (`getComputedStyle(...)
  .getPropertyValue('--gain')` etc., and the `Chart` instantiation) lives in a same-origin
  `static/portfolio_chart.js`, loaded exactly like vendored htmx. `test_templates.py`'s new
  `test_templates_have_no_inline_executable_script` makes the "no inline `<script>` with logic"
  invariant mechanical rather than only reviewed by eye. The chart uses a `category` x-axis, not
  Chart.js's `time` scale, to avoid vendoring a second date-adapter dependency just to plot daily
  points — a calendar gap renders as an equal-width step rather than a proportionally wider one,
  an accepted v1 simplification. Since UI-3 it reads the semantic tokens (`--gain`, `--loss`,
  `--border`, `--text-muted`), not brand tokens, so it follows the theme. A canvas doesn't
  restyle itself, so it re-reads them and redraws on `stakehold:themechange` (the header toggle)
  and on a `prefers-color-scheme` change.
- **The chart's view toggle and date ranges (UI-6) work client-side over the one data island.**
  The island carries `market_value_eur` and `total_return_eur` per day, so switching between
  "Total return" and "Market value", or between 1M / 6M / 1Y / All, never makes a request. A range
  counts back from the *last data point's* date, not from today, so an account whose prices
  stopped updating still shows its final stretch rather than an empty chart. Total return is a
  delta, so its line keeps the gain/loss segment colors. Market value is a level, so it's drawn in
  `--text`, because forest and oxblood mean gains and losses only, and gold is kept for controls.
  The buttons are in the template, not built by the script. They ship `hidden`, because without JS
  they'd do nothing, and the script reveals them. Building them in JS would also put their classes
  outside `test_templates.py`'s template/CSS class check. Only the first draw animates: morphing
  one range into another, with a different point count, draws stray lines. Tooltip and y-axis
  labels use a small formatter in `portfolio_chart.js` that mirrors `formatting.py`'s
  `money`/`signed_money`. **Known sharp edge:** that is two copies of one convention, and no test
  ties them together (there's no JS test runner). A change to the convention must touch both.
- **Theme (UI-3): a cookie, applied on the server.** The choice (`light` / `dark` / `system`,
  default `system`) is stored in a `stakehold_theme` cookie, set by `POST /theme`
  (`api/theme.py`). A Jinja2Templates context processor (`theme_context`, registered in
  `app.py`) gives every template a `theme`, including pages rendered outside `routes.py` such as
  the 403 page. `base.html` renders `data-theme="light|dark"` on `<html>` from it, or no
  attribute for `system`. This is server-side because the alternative, localStorage read by a
  script, would have to run before first paint to avoid a flash of the wrong theme. That would
  mean an inline `<script>` in `<head>`, which `script-src 'self'` forbids. A same-origin
  `defer` script would run too late. The cookie is `HttpOnly`: only the server reads it, and JS
  learns the theme from the attribute or the htmx trigger below.
  - *CSS*: dark applies on `:root[data-theme="dark"]` (a manual choice) and on
    `:root:not([data-theme="light"])` inside `@media (prefers-color-scheme: dark)` (no manual
    choice, OS dark). CSS can't share one declaration block between an attribute selector and a
    media query, so the dark aliases are written twice; `test_templates.py` asserts the two
    copies are identical, and that every color alias the light block sets is re-pointed.
    `color-scheme` is set alongside (`light dark` by default, one scheme per choice) so native
    controls, scrollbars and the date picker follow. Rejected: CSS `light-dark()`, which would
    need one block only. `getPropertyValue` returns an alias as the unresolved
    `light-dark(...)` text, so the chart couldn't read its colors. It's also only Baseline since
    2024.
  - *Logo*: both `logo-stone.svg` and `logo-gold.svg` are in the header, and CSS shows one,
    using the same selectors as the dark blocks. A server-chosen `<img>` plus `<picture>` for
    `system` would also need JS to rebuild the element after an in-place switch. The cost is one
    extra small SVG download per page. The hidden image is `display: none`, so screen readers
    skip it.
  - *Toggle*: a form in the header with three submit buttons (`name="theme"`), the current one
    marked `aria-pressed="true"`. It's shown on every page rendered with a CSRF token, logged in
    or not; the 403 page has no token, so no toggle. Without JS it posts and gets a 303 back to
    the page it came from (`next`, the current path and query). With htmx (`hx-swap="none"`)
    the route answers `200` with `HX-Trigger: {"stakehold:theme": "<value>"}`, and
    `static/theme.js` sets the attribute and `aria-pressed`, then fires
    `stakehold:themechange` for the chart.
  - *`next` is a redirect target*, so `theme.safe_next` only accepts a same-site path. It refuses
    `//host`, `/\host`, a scheme, and any control character or non-ASCII. Browsers strip tabs
    and newlines, so `/\t/host` would otherwise become `//host`. Anything refused redirects to
    `/`. Known sharp edge: on a page re-rendered after a failed POST (a form error), `next` is
    the POST's own URL, so the no-JS toggle redirects to that route's GET. For
    `/transactions/new` and `/transactions/import`, the account is lost from the query and the
    GET answers 422. The htmx path doesn't navigate, so it's unaffected.
- **`GET /portfolio` is per-account, read-only, and never triggers a rebuild.** Every `portfolio`
  positions/returns endpoint is `account_label`-scoped with no cross-account aggregation endpoint,
  so the view is one page per account (linked from `accounts.html`); a cross-account "whole
  portfolio" rollup is future work. The route only displays whatever `position_snapshots`/
  `return_snapshots` rows already exist. `web` never calls a rebuild itself: `portfolio` rebuilds
  an account's positions and returns on every transaction write (see `portfolio/docs/architecture.md`,
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
- **App shell (UI-4): the current page is marked on the server, and the account pages share one
  header.** Each route passes `nav_active` (`overview` / `accounts` / `admin`) and, on the four
  account-scoped pages, `account_nav_active` (`holdings` / `transactions` / `add` / `import`). The templates turn these into `aria-current="page"`, and `web.css` styles
  that attribute rather than a class. The mark a screen reader announces and the one a sighted
  visitor sees therefore come from one source and can't disagree, and no script works out "where
  am I" from the URL. `partials/_account_header.html` (label, broker, sub-nav, switcher) is fed by
  `routes._account_header_context`, one `list_accounts` call. The same call answers "is this the
  visitor's account?": `/transactions/new` and `/transactions/import` used to render a form for
  any `?account=` label and leave `portfolio` to reject the write. They now 404 up front
  (`account_not_found.html`, shared with `/portfolio`). The 404 happens only when the list loaded
  and the label isn't in it. If `portfolio` can't be reached, the page renders with a label-only
  header and no switcher, because an outage must not tell the visitor a real account doesn't
  exist. POST routes load the header lazily (`extra_context_loader`), so only a full-page error
  re-render pays for that call, never a successful write.
- **The account switcher is a plain GET form, with no htmx enhancement.** The form submits
  `?account=` back to the current path, so switching keeps the visitor on Holdings,
  Transactions, Add or Import. An htmx `hx-get` swapping the response into `<body>` was planned and rejected. htmx
  1.9 does take a full document's `<body>` and update the title, but page scripts that start on
  `DOMContentLoaded` (`portfolio_chart.js`) never run after a swap, so `/portfolio` would lose its
  chart. A full navigation is also what an account switch is: every figure on the page changes.
  The cost is a visible "Switch" button, since the form can't submit on `change` without a script.
  The Transactions sub-nav link shipped ahead of its page (UI-4) and 404ed until UI-8 built
  `GET /transactions`.
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
- **Display formatting (UI-5) is one fixed convention, applied by Jinja filters from a pure
  module.** `formatting.py` defines `money`, `signed_money`, `qty`, `pct` and `date` (plus UI-6's
  `share`, an unsigned percentage for weights, and `delta_class`), registered on
  the templates environment in `app.py`'s lifespan. Templates never round or append units
  themselves. The convention: English digit grouping (`€1,234.50`), a `€` prefix because every
  aggregated figure `portfolio` returns is EUR, a true minus sign (U+2212) instead of a hyphen,
  `pct` taking a fraction (`0.0184` → `+1.84%`), and ISO dates (`2026-10-06`, the format the admin
  mappings page already used, and unambiguous whatever the reader's locale). Rounding is half-up on
  the `Decimal`, not Python's default banker's rounding, since a displayed cent should round the
  way a reader expects. The sign is chosen *after* rounding, so a value that rounds to zero shows
  no sign: a zero delta is neither a gain nor a loss, and `−€0.00` would be noise. `qty` trims
  trailing zeros but never falls into scientific notation (`Decimal.normalize()` alone renders 100
  as `1E+2`). Every filter renders `—` for `None`. Locale switching is out of scope; if it's ever
  needed, Babel would replace the bodies of these functions and nothing else. The module has
  no Jinja or FastAPI import so it can be tested on its own, and so that `portfolio_chart.js` has
  a single, readable reference for the rules it mirrors (UI-6). `delta_class` returns `gain` /
  `loss` / nothing from the sign a figure *displays* with, after the same rounding as
  `signed_money` or `pct`. A delta's color therefore can't disagree with its printed sign: `-0.004`
  prints `€0.00` and is uncolored, where a `>= 0` test in the template would have colored it a gain.
  UI-8 added `native_money(value, currency, places=2)` for a transaction's own figures, which
  aren't EUR: the currency code follows the number (`−1,503.50 USD`), and the sign is `money`'s
  (`−` only) because it's a cash-flow direction, not a delta. Prices use `places=4` and trim
  zeros past the second decimal (`112.34 USD`, `0.1235 USD`).

## Endpoint surface

| Route | Behaviour |
|---|---|
| `GET /` | `require_user` dependency: logged in → the placeholder home page (the nav's "Overview"); no valid session → redirected to `/login` (an `AuthRequired` exception mapped to a 303 by an app-level handler) |
| `GET /admin` | `require_admin` dependency: an admin → the admin page (a link to ticker mappings), linked from the nav only for admins; a logged-in non-admin → a 403 page (`AdminRequired`, mapped by an app-level handler); no valid session → redirected to `/login`, like every `require_user` route |
| `GET /admin/mappings` | `require_admin`; fetches `portfolio`'s `GET /ticker-mappings`, `GET /instruments/unmapped/all` and `GET /instruments/tickers`, and renders three things: the instruments still waiting for a mapping (each with an inline "map to ticker" form), the existing mappings (each with a delete button), and a form to add or re-point one. Each list degrades on its own to a notice rather than an empty table, so an outage never reads as "nothing to map". After a save or delete, the redirect's query string (`saved`/`deleted`, `rebuilt`, `failed`) drives a one-line outcome banner |
| `POST /admin/mappings` | `require_admin`, CSRF-checked; calls `portfolio`'s `PUT /ticker-mappings` with the admin's own `user_id` as `updated_by` (a blank broker is sent as `null`, which an ISIN needs; portfolio enforces which kind needs one and its 422 message is shown as-is). Redirects to `/admin/mappings?saved=1&rebuilt=<n>&failed=<m>` |
| `POST /admin/mappings/{id}/delete` | `require_admin`, CSRF-checked; calls `DELETE /ticker-mappings/{id}`. POST rather than DELETE because a plain HTML form can't send DELETE and the page has no script. An already-deleted mapping (portfolio's 404, mapped to `TickerMappingNotFound` on this route only) re-renders with "That mapping was already deleted." |
| `GET /login`, `GET /signup` | `optional_user` dependency: already logged in → redirected to `/`; otherwise renders the form with a fresh or existing CSRF cookie |
| `POST /login`, `POST /signup` | CSRF-checked, calls `auth`, proxies the `Set-Cookie` `auth` returned, then redirects to `/` — a 303 for a plain form post, or an `HX-Redirect` response header for an `hx-post` (htmx's own XHR follows a 303 by swapping the *content* of the request's own response into the target element, which would drop a full HTML document into a form's error slot; `HX-Redirect` instead triggers a real browser navigation) |
| `POST /theme` | No login needed. CSRF-checked: a mismatch changes nothing and redirects back (an `HX-Redirect` for htmx), like `POST /logout`, rather than re-rendering a form error. A value other than `light`/`dark`/`system` → `400`. Otherwise sets the `stakehold_theme` cookie (`HttpOnly`, `SameSite=Lax`, `Path=/`, one year, `Secure` per `WEB_COOKIE_SECURE`), then a 303 to the validated `next`, or for htmx a `200` with `HX-Trigger`. See "Theme" under "Key decisions" |
| `POST /logout` | CSRF-checked, calls `auth` (best-effort — a failure is logged, not surfaced, since the user's intent to log out should not depend on `auth` being reachable), clears the session cookie locally, redirects to `/login` |
| `GET /health` | `200 {"status": "ok"}`, unauthenticated, no dependency check (see "Key decisions") |
| `GET /accounts` | `require_user`; lists the caller's accounts (`portfolio`'s `GET /accounts`) and installed brokers (`GET /brokers`) and renders the create-account form, whose broker field is a `<select>` of those brokers plus "Custom" (see "Key decisions") |
| `POST /accounts` | CSRF-checked. With "Custom" chosen (`broker=__custom__`), sends the typed `custom_broker` name instead, after refusing a blank one or one matching an installed broker ignoring case (both 422, no `portfolio` write). Calls `portfolio`'s `POST /accounts`, redirects to `/accounts` on success |
| `GET /transactions/new` | `require_user`; required `account` query param (a bare 422 for a missing/blank value — reachable only via a hand-crafted URL, since every link that points here already includes it); renders the manual-entry form, with a confirmation banner if `?saved=<n>` (plus the rebuild outcome from `positions`/`returns`; see "Key decisions"). The instrument field is a ticker picker: a native `<datalist>` filled from `portfolio`'s `GET /instruments/tickers` (no script, so the strict CSP is untouched). If that call fails, the form still renders with an empty list and a notice; the picker is a convenience, and portfolio checks whatever is typed when it's saved. Also fetches `GET /accounts` (concurrently) for the account header: a label that isn't one of the visitor's accounts renders `account_not_found.html` with a 404, and a failed fetch degrades to a label-only header rather than a 404 |
| `POST /transactions/new` | CSRF-checked, calls `portfolio`'s `POST /transactions` (an instrument, when given, is always sent as `instrument_identifier_kind = 'TICKER'`; a blank Amount is sent as `null`, for portfolio to derive on a BUY/SELL or reject as missing on anything else), redirects to `/transactions/new?account=<label>&saved=<transactions_saved>&positions=<status>&returns=<status>` on success. An unknown ticker comes back as portfolio's 422, whose message is shown as-is like every other 422. The ticker list is re-fetched for the error re-render |
| `GET /transactions/import` | `require_user`; required `account` query param; renders the upload form with an importer `<select>` listing the account's own broker's importers, with a confirmation banner (row count and rebuild outcome) if `?imported=<n>`. A broker with no importer gets a pointer to manual entry instead of a form; brokers that can't be listed get an inline notice, never the "no importer" text. Fetches `GET /accounts` (for the account header, with the same 404 and degrade rules as `GET /transactions/new`) and `GET /brokers` concurrently |
| `POST /transactions/import` | CSRF-checked, multipart; `importer` must be `transactions` or `cashflow` (anything else is a 422 with no `portfolio` call); size-capped at 20MB (matching `portfolio`'s own cap) before the bytes are forwarded; calls `portfolio`'s `POST /transactions/import` or `POST /cashflows/import` to match, redirects to `/transactions/import?account=<label>&imported=<rows_saved>&positions=<status>&returns=<status>` on success |
| `GET /transactions` | `require_user`; required `account` query param; fetches `portfolio`'s `GET /transactions` and `GET /accounts` (for the account header) concurrently, and renders the log newest first (reversed in `web`; see "Key decisions"): date, type badge, instrument, quantity, price, amount (with the EUR amount under it on a non-EUR row), fees and reference. No pagination. An empty log shows links to Add and Import. `AccountNotFound`, or a label missing from a successfully loaded account list, renders `account_not_found.html` with a 404; any other failure renders a banner and no table, never the empty state; a failed account list degrades to a label-only header |
| `GET /portfolio` | `require_user`; required `account` query param; fetches `portfolio`'s `GET /positions`, `GET /returns`, `GET /returns/latest` and `GET /instruments/unmapped` concurrently. Renders a "Prices as of" line and summary tiles (market value, cost basis, total return € and %, dividends; `portfolio_view.summarize_latest_returns`), a holdings table of open positions joined to their latest return row (quantity, average cost, market value, weight, unrealized return; `portfolio_view.build_holdings`), and a Chart.js chart of the per-ticker series aggregated into one portfolio-level series (`portfolio_view.aggregate_portfolio_returns`), with a total return / market value toggle and date ranges. `GET /accounts` is fetched concurrently for the account header. `AccountNotFound` on any call, or a label missing from a successfully loaded account list, renders `account_not_found.html` with a 404; any other failure degrades only the part that call feeds, mirroring `GET /accounts`'s own pattern. Pending states: "Waiting for prices" on a holding with no return row, and a notice listing the instruments waiting for a ticker mapping at this account's broker (UI-7). No writes, no rebuild trigger. |

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
  `portfolio`'s order unchanged; the route reverses it.
- `rebuild_outcome.py` (UI-7) — pure. Turns the two rebuild statuses into the confirmation
  banner's one sentence; see "Key decisions".
- `portfolio_view.py` (task 12) — pure, I/O-free reshaping of what `routes.py` fetches for
  `GET /portfolio`: `filter_open_positions`, `aggregate_portfolio_returns`, and (UI-6)
  `summarize_latest_returns` and `build_holdings` with its `HoldingRow`, and (UI-7)
  `unmapped_for_broker` (see "Key decisions"). No FastAPI/httpx dependency, tested in isolation, mirroring how `portfolio`'s own
  `returns/calculator.py` is a pure function.
- `formatting.py` (UI-5) — pure display formatters (`money`, `signed_money`, `qty`, `pct`,
  `date`, UI-6's `share` and `delta_class`, and UI-8's `native_money`) plus the `FILTERS` mapping `app.py` registers as Jinja filters; see "Key decisions".
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
  target; `partials/_account_header.html`, included by the four account-scoped pages; and
  `account_not_found.html`, their shared 404) and `web.css` plus vendored `htmx.min.js`.
  Same-origin scripts: `htmx-error-swap.js`, `portfolio_chart.js`, and `theme.js` (UI-3, applies
  a theme switch in place).
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
  `WEB_PORTFOLIO_INTERNAL_SECRET` as `X-Internal-Secret`. Rebuild triggers aren't called from
  `web`: `portfolio` rebuilds positions and returns itself on each transaction write, and
  refreshes returns on a schedule. `web` only reports the write's rebuild statuses (UI-7).
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
