# Tasks

Roadmap for the UI/UX chunk: making `web` look and feel like a finished product rather than a
walking skeleton, in both aesthetics (a real design system, dark mode) and UX (navigation, readable
figures, data that refreshes from the UI, forms that fit the task). The previous chunk, the
walking skeleton across all four services, is complete and archived in
[`docs/roadmap-archive/walking-skeleton.md`](docs/roadmap-archive/walking-skeleton.md).

**Task IDs are prefixed `UI-`** so they can't be confused with the walking-skeleton's own task
numbers. Code comments and docs throughout the repo say "task 9", "task 13", and so on; a bare
"task N" always means walking-skeleton task N.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency (or, for UI-2, on the user's sign-off).

## Constraints that apply to every task

- **Brand**: colors, type and shape come only from `brand/tokens.css` custom properties, never a
  hardcoded hex value. `services/web/tests/unit/test_templates.py` enforces this mechanically for
  `web.css` and every template, so new theme values (dark mode included) belong in `tokens.css`.
  Check the relevant chapter of `brand/brand-charter.html` before building a component.
- **Forest and oxblood mean gains and losses, nothing else.** Error states, destructive actions and
  warnings must not use oxblood (UI-2 defines what they use instead). Copy follows chapter IV of
  the charter: plain verbs, no hype, no panic.
- **CSP stays `script-src 'self'`**: no inline executable `<script>`, no CDN. Behaviour goes in a
  same-origin file under `static/`; server data reaches JS through a
  `<script type="application/json">` data island (the pattern `portfolio.html` already uses, and
  `test_templates_have_no_inline_executable_script` enforces).
- **Progressive enhancement**: every form still works as a plain full-page POST without JS.
  htmx and small scripts improve it; they don't gate it.
- **No new `portfolio` endpoints in this chunk.** `web` may call any endpoint `portfolio` already
  exposes. Work needing new ones is listed under [Deferred](#deferred).
- **Tests first**, per `.claude/rules/testing.md`. **`services/web/docs/architecture.md` is updated
  in the same change** whenever a task adds a route, a client method, a module or a static asset
  with behaviour, per `.claude/rules/architecture.md`.

## Status table

| ID | Task | Depends on | Status |
|----|------|-------------|--------|
| UI-1 | [Design-system foundation](#ui-1-design-system-foundation) | — | ready |
| UI-2 | [Dark palette proposal & brand sign-off](#ui-2-dark-palette-proposal--brand-sign-off) | — | ready |
| UI-3 | [Dark mode](#ui-3-dark-mode) | UI-1, UI-2 | blocked |
| UI-4 | [App shell & navigation](#ui-4-app-shell--navigation) | UI-1 | blocked |
| UI-5 | [Number & date formatting](#ui-5-number--date-formatting) | — | ready |
| UI-6 | [Account portfolio page redesign](#ui-6-account-portfolio-page-redesign) | UI-4, UI-5 | blocked |
| UI-7 | [Refresh data from the UI](#ui-7-refresh-data-from-the-ui) | UI-6 | blocked |
| UI-8 | [Transactions list](#ui-8-transactions-list) | UI-4, UI-5 | blocked |
| UI-9 | [Transaction entry form UX](#ui-9-transaction-entry-form-ux) | UI-1 | blocked |
| UI-10 | [Accounts & import pages](#ui-10-accounts--import-pages) | UI-4 | blocked |
| UI-11 | [Home dashboard](#ui-11-home-dashboard) | UI-6 | blocked |
| UI-12 | [Feedback, loading & error states](#ui-12-feedback-loading--error-states) | UI-1, UI-2 | blocked |
| UI-13 | [Responsive & accessibility pass](#ui-13-responsive--accessibility-pass) | UI-3, UI-6, UI-7, UI-8, UI-9, UI-10, UI-11, UI-12 | blocked |

UI-1, UI-2 and UI-5 have no dependencies and can be done in any order. UI-2 is mostly a proposal
and a decision, not code. UI-3 (dark mode) is the first task to need both UI-1 and UI-2.

## Tasks

### UI-1. Design-system foundation

**Status:** ready

Preparatory refactoring, done first so that every later task (and dark mode in particular) is a
token swap rather than a rule-by-rule rewrite. Today `web.css` styles `.auth-card`, `.welcome-card`
and `.entry-card` as three near-identical blocks, references brand color tokens directly in every
rule, and has no focus styles, button variants, spacing scale or responsive rules.

- A **semantic token layer** at the top of `web.css`: `--surface`, `--surface-raised`, `--text`,
  `--text-muted`, `--border`, `--accent`, `--gain`, `--loss`, `--focus-ring`, each aliased to a
  brand token. Every other rule in `web.css` uses only semantic tokens, so UI-3 only has to
  re-point the aliases.
- A **spacing scale** (`--space-1` … `--space-8`) and a content max-width.
- Shared components: one `.card` (replacing the three card classes), `.field` (label, input, help
  text, error), and `.btn` with `primary` (gold), `secondary` and `ghost` variants. Visible
  `:focus-visible` on every interactive element.
- Templates migrated to the new class names. No route or behaviour change.

Files: `services/web/src/web/static/web.css`, every template under `templates/`,
`tests/unit/test_templates.py`.

Done when: every page looks essentially unchanged in light mode; a new test asserts that `web.css`
references brand color tokens (`--stone`, `--parchment`, `--gold`, …) only inside the semantic
alias block; the existing hex-scan and no-inline-script tests still pass.

### UI-2. Dark palette proposal & brand sign-off

**Status:** ready

The brand defines light surfaces only, plus `--forest-on-dark` and `--oxblood-on-dark` for figures
on a stone card. Dark mode needs a full set, and the palette is the user's to own.

- Propose dark values for: page background (a deeper stone), raised card surface, primary text
  (parchment), muted text, hairline, and focus ring. Gold stays the one accent, unchanged unless
  contrast forces a lighter variant. Gains and losses use `--forest-on-dark` and
  `--oxblood-on-dark`.
- Propose an **error/warning treatment that isn't oxblood**, for both themes (for example a
  gold-ruled banner with ordinary text color and an icon), since red is reserved for losses.
- Check WCAG AA contrast (4.5:1 body text, 3:1 large text and UI boundaries) for every
  text/surface pair in both themes, and record the ratios.
- Add a "Dark surfaces" section to chapter II of `brand/brand-charter.html`, plus the error
  treatment, covering which logo goes on which surface (`logo-gold.svg` on dark, per chapter I).

**Gate**: present the values to the user and get explicit sign-off before UI-3 builds on them. Only
then do the tokens land in `brand/tokens.css`. Flip to `done` on sign-off plus the charter update.

### UI-3. Dark mode

**Status:** blocked

Depends on UI-1 (semantic aliases to re-point) and UI-2 (signed-off values).

- `brand/tokens.css` gains the dark values under both `:root[data-theme="dark"]` (manual choice)
  and `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { … } }` (system
  default), and sets `color-scheme` so native form controls, scrollbars and the date picker follow.
- **Choice persisted in a cookie** (`theme` = `light` | `dark` | `system`, default `system`). A new
  CSRF-checked `POST /theme` sets it and redirects back. `base.html` renders `data-theme` on
  `<html>` from the cookie **on the server**, so a manual choice applies with no flash of the
  wrong theme and no inline script. `system` renders no attribute and leaves it to the media
  query.
- A header toggle: a small form (light / dark / system) that works as a plain POST and is
  htmx-enhanced to switch without a full reload.
- The logo swaps between `logo-stone.svg` and `logo-gold.svg` by theme: `<picture>` with a
  `prefers-color-scheme` `<source>` for `system`, and a server-chosen `<img>` for a manual
  choice. The mark itself is never recolored.
- `portfolio_chart.js` reads the semantic tokens (`--gain`, `--loss`, `--border`, `--text-muted`)
  rather than raw brand tokens, and redraws when the theme changes (a `matchMedia` listener plus
  the toggle's swap event).

Files: `brand/tokens.css`, `templates/base.html`, `api/routes.py` (or a small new module),
`static/portfolio_chart.js`, `static/theme.js` (new, if htmx alone isn't enough), `docs/architecture.md`.

Tests: `POST /theme` (each valid value, invalid value rejected, CSRF required, cookie attributes);
`data-theme` rendered correctly per cookie value, including no cookie; hex-scan still green.

### UI-4. App shell & navigation

**Status:** blocked

Depends on UI-1. Today the nav has one "Portfolio" link that goes to `/accounts`, with no active
state; nothing ties an account's pages together.

- Header nav: **Overview** (`/`) and **Accounts** (`/accounts`), with an active state driven by a
  template variable each route sets.
- On account-scoped pages (`/portfolio`, `/transactions`, `/transactions/new`,
  `/transactions/import`): a shared account header with the account label and broker, a sub-nav
  (Holdings / Transactions / Add / Import), and an account switcher (a plain `<select>` +
  submit, htmx-enhanced) built from `list_accounts`.
- A skip-to-content link, a contained page width, and a header that collapses gracefully at
  phone width.
- A shared `partials/_account_header.html` so the four pages don't each re-implement it.

Done when: every page is reachable from the nav without typing a URL, and the current page is
visibly marked.

### UI-5. Number & date formatting

**Status:** ready

Figures are printed as raw `Decimal`s today: `average_cost_eur` at full precision, no thousands
separators, percentages computed inline in the template (`(pct * 100) | round(2)`).

New pure module `services/web/src/web/formatting.py`, registered as Jinja filters on the templates
environment:

- `money`: `1234.5` → `€1,234.50` (2 decimal places, thousands separators; EUR only for now, since
  every aggregated figure is EUR).
- `signed_money`: as `money`, with an explicit `+` or `−` sign, for deltas.
- `qty`: trims trailing zeros (`10.000000` → `10`, `0.125000` → `0.125`).
- `pct`: takes a fraction, renders signed with 2 decimal places (`0.0184` → `+1.84%`).
- `date`: one fixed, unambiguous display format (e.g. `6 Oct 2026`).

Each returns `—` for `None`. Templates are switched over to the filters (only `portfolio.html` and
the import confirmation today). Pick one locale convention and record it in
`docs/architecture.md`; locale switching is out of scope.

Tests: `tests/unit/test_formatting.py` covering `None`, zero, negative, large and many-decimal
inputs, and the `−` sign character.

### UI-6. Account portfolio page redesign

**Status:** blocked

Depends on UI-4 (account header and sub-nav) and UI-5 (formatting). `/portfolio?account=` shows
only quantity, average cost and cost basis today, so a user can't see what the holding is
actually *worth*.

- **Summary tiles** at the top: market value, cost basis, total return (€ and %), and dividends
  received, from a new `PortfolioClient.list_latest_returns` method calling the existing
  `GET /returns/latest`, aggregated in `portfolio_view.py`.
- **Holdings table** joining each open position to its latest return row: ticker, quantity,
  average cost, market value, weight (% of account), and unrealized return (€ and %), with
  figures in mono and gain/loss coloring on the deltas only. Closed positions stay filtered out
  (`filter_open_positions`).
- **Chart**: formatted tooltips and y-axis (via the UI-5 conventions, mirrored in JS), a toggle
  between market value and total return, and range buttons (1M / 6M / 1Y / All) filtered
  client-side over the existing data island (no new request).
- An "as of <date>" line, from the latest return row's `price_date`.

Files: `portfolio_client/{base,http_client}.py`, `portfolio_view.py`, `api/routes.py`
(`_load_portfolio_view`), `templates/portfolio.html`, `static/portfolio_chart.js`.

Tests: the new client method (happy path, 404, malformed body); the join/weight computation in
`portfolio_view.py` (a position with no return row yet, zero total value, rounding); route tests
for the tiles and the degrade-on-partial-failure behaviour UI-6 must keep.

### UI-7. Refresh data from the UI

**Status:** blocked

Depends on UI-6. Positions and returns are only rebuilt from the CLI today, and the empty state
literally tells the user to "run rebuild-returns", although `portfolio` already exposes
`POST /positions/rebuild` and `POST /returns/rebuild`.

- A **Refresh** button on the account page posts to a new CSRF-checked
  `POST /portfolio/refresh?account=`. That route calls rebuild-positions and then rebuild-returns
  through two new `PortfolioClient` methods, then re-renders the page.
- The outcome is reported plainly: rows saved, and any `blocked` or `failed_pairs` tickers by name.
  Unmapped instruments (`GET /instruments/unmapped`, a third new client method) are listed with a
  hint that they need a ticker mapping.
- htmx loading state on the button while it runs (rebuild-returns can take seconds).
- All CLI wording is removed from empty states.

Open question, to settle with the user at the start of the task: should saving a transaction or
importing a file also trigger a refresh automatically, or offer a "Refresh now" link?

Tests: client methods (including the `partial` result shape); route success, partial, and
`DataStoreApiError` → 502-mapped error, each rendering an appropriate message.

### UI-8. Transactions list

**Status:** blocked

Depends on UI-4 and UI-5. There's no way to see what has been entered for an account, although
`portfolio` exposes `GET /transactions`.

- New `GET /transactions?account=`: a table of the account's transactions, newest first, showing
  date, type (as a neutral badge, never gain/loss colored), instrument, quantity, price, and
  amount + currency, plus an empty state linking to Add / Import.
- New `PortfolioClient.list_transactions` method and a `Transaction` dataclass mirroring
  `portfolio`'s `TransactionResponse`.
- Reached from the account sub-nav (UI-4). Pagination is deferred unless the endpoint already
  supports it; check before building.

Tests: client method; route (login required, happy path, empty, unknown account → 404).

### UI-9. Transaction entry form UX

**Status:** blocked

Depends on UI-1. `/transactions/new` shows 12 fields at once regardless of type, requires a
hand-typed reference, takes the currency as free text, and shows the FX field even for EUR.

- Fields grouped into fieldsets: *What* (type), *When* (date), *Amount* (amount, currency, FX
  rate, fees), *Instrument* (identifier, kind, quantity, price).
- A new `static/transaction_form.js` shows only the fields relevant to the selected type (for
  example instrument, quantity and price for BUY/SELL; no instrument for a deposit), and shows FX
  rate only when the currency isn't EUR. Hidden fields are cleared so they aren't submitted.
  Without JS every field is shown, exactly as today. Server-side validation is unchanged and stays
  the source of truth.
- Reference prefilled server-side with a generated, editable `manual-<uuid4>`. It stays editable
  because it is the idempotency key, so a user re-entering a known broker reference can still do so.
- Currency as a `<select>` (or input + datalist) of the currencies `data-store` supports,
  defaulting to EUR. Date defaults to now. Short help text on the less obvious fields (reference,
  FX rate direction, identifier kind).
- The type→fields mapping is confirmed against `transactions/base.py`'s `validate()` in
  `portfolio`, not guessed.

Tests: the generated reference is present and unique per render; the form still posts every
field without JS (template test); existing submit tests unchanged.

### UI-10. Accounts & import pages

**Status:** blocked

Depends on UI-4.

- **Accounts**: the account list comes first as rows or cards, each with its broker and one
  primary action (open holdings) plus secondary links. "New account" moves to a secondary
  `<details>` disclosure, open automatically when the user has no accounts. A first-run empty
  state explains the three steps: create an account, add or import transactions, refresh.
- **Import**: a styled file drop zone (a real `<input type="file">` underneath, so it works without
  JS), the account and broker shown, the 20 MB limit stated, and after a successful import a
  result summary with next steps (Refresh, from UI-7 if done, and view transactions).

Tests: route/template tests for the empty-state disclosure behaviour and the import summary.

### UI-11. Home dashboard

**Status:** blocked

Depends on UI-6 (latest-returns client method and per-account aggregation). Home is a welcome card
with one link today.

- `/` becomes an **overview across all accounts**: total market value, total cost basis and total
  return (€ and %), then one row per account (label, broker, market value, total return) linking
  to its holdings.
- Cross-account aggregation is a new pure function in `portfolio_view.py`, next to
  `aggregate_portfolio_returns`. The percentage is recomputed from summed euros, never averaged,
  for the same reason that function documents.
- One `list_latest_returns` call per account, made concurrently. One account failing degrades its
  row, not the page.
- A first-run state with no accounts that points to creating one.

Tests: the aggregation function (no accounts, an account with no returns yet, mixed
gains/losses); route tests for the overview, the empty state and a single failing account.

### UI-12. Feedback, loading & error states

**Status:** blocked

Depends on UI-1 and UI-2 (the error treatment).

- Styled success and error banners (`partials/_form_errors.html` and the confirmation banners)
  using UI-2's error treatment. `#form-errors` becomes an `aria-live="polite"` region.
- htmx `hx-indicator` plus a disabled submit button while any form request is in flight, so a slow
  submit can't be double-posted.
- Branded 404 and 5xx pages (extending `base.html`, plain copy) instead of FastAPI's JSON or
  Starlette's default error pages.
- Auth pages: centered card with the mark, a password show/hide toggle (external JS), and the
  12-character minimum stated up front on signup instead of only on failure.

Tests: exception-handler routes render the branded pages with the right status codes; the
`aria-live` attribute is present; existing htmx error-swap tests still pass.

### UI-13. Responsive & accessibility pass

**Status:** blocked

Last, once every page exists in its new form.

- Tables (holdings, transactions) reflow at phone width (stacked rows or a horizontally
  scrollable container with a visible cue). No page scrolls horizontally at 360 px.
- Contrast audit of every page in both themes against WCAG AA; fix any regressions.
- Keyboard-only walkthrough of every flow (signup → account → add → refresh → view), with visible
  focus throughout.
- A Playwright screenshot script under `services/web/` (Playwright is already a workspace
  dependency, used by `data-store`) that captures every page in light and dark at desktop and
  phone width, for review rather than as a pixel-diff gate.

## Deferred

Out of this chunk because each needs a new `portfolio` endpoint (and possibly a schema discussion
per `.claude/rules/data-schema.md`), which this chunk deliberately excludes:

- Editing or deleting a transaction.
- Renaming or deleting an account.
- A server-side cross-account return-over-time series (UI-11 shows the current snapshot only).
- Pagination or filtering of transactions, if `GET /transactions` doesn't already support it.
