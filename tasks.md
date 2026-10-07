# Tasks

Roadmap for the UI/UX chunk: making `web` look and feel like a finished product rather than a
walking skeleton, in both aesthetics (a real design system, dark mode) and UX (navigation, readable
figures, visible data freshness, forms that fit the task). The previous chunk, the
walking skeleton across all four services, is complete and archived in
[`docs/roadmap-archive/walking-skeleton.md`](docs/roadmap-archive/walking-skeleton.md).

**Task IDs are prefixed `UI-`** so they can't be confused with the walking-skeleton's own task
numbers. Code comments and docs throughout the repo say "task 9", "task 13", and so on; a bare
"task N" always means walking-skeleton task N.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency.

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
| UI-1 | [Design-system foundation](#ui-1-design-system-foundation) | — | done |
| UI-2 | [Dark palette proposal & brand sign-off](#ui-2-dark-palette-proposal--brand-sign-off) | — | done |
| UI-3 | [Dark mode](#ui-3-dark-mode) | UI-1, UI-2 | done |
| UI-4 | [App shell & navigation](#ui-4-app-shell--navigation) | UI-1 | done |
| UI-5 | [Number & date formatting](#ui-5-number--date-formatting) | — | done |
| UI-6 | [Account portfolio page redesign](#ui-6-account-portfolio-page-redesign) | UI-4, UI-5 | done |
| UI-7 | [Data freshness & rebuild feedback](#ui-7-data-freshness--rebuild-feedback) | UI-6 | done |
| UI-8 | [Transactions list](#ui-8-transactions-list) | UI-4, UI-5 | done |
| UI-9 | [Transaction entry form UX](#ui-9-transaction-entry-form-ux) | UI-1 | ready |
| UI-10 | [Accounts & import pages](#ui-10-accounts--import-pages) | UI-4 | ready |
| UI-11 | [Home dashboard](#ui-11-home-dashboard) | UI-6 | ready |
| UI-12 | [Feedback, loading & error states](#ui-12-feedback-loading--error-states) | UI-1, UI-2 | ready |
| UI-13 | [Responsive & accessibility pass](#ui-13-responsive--accessibility-pass) | UI-3, UI-6, UI-7, UI-8, UI-9, UI-10, UI-11, UI-12, UI-14 | blocked |
| UI-14 | [Broker dropdown & importer choice](#ui-14-broker-dropdown--importer-choice) | UI-4 | done |

UI-1, UI-2 and UI-5 have no dependencies and can be done in any order. UI-2 is mostly a proposal
and a decision, not code. UI-3 (dark mode) is the first task to need both UI-1 and UI-2.

## Tasks

### UI-1. Design-system foundation

**Status:** done

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
- Templates migrated to the new class names, including the admin pages (`admin.html`,
  `admin_mappings.html`) and `forbidden.html` added by walking-skeleton tasks 16–17. No route or
  behaviour change.

Files: `services/web/src/web/static/web.css`, every template under `templates/`,
`tests/unit/test_templates.py`.

Done when: every page looks essentially unchanged in light mode; a new test asserts that `web.css`
references brand color tokens (`--stone`, `--parchment`, `--gold`, …) only inside the semantic
alias block; the existing hex-scan and no-inline-script tests still pass.

As built:
- One semantic token added beyond the list above: `--on-accent`, for text on a gold button. It
  stays stone in either theme, while `--text` flips, so UI-2/UI-3 must give it a dark value
  separately (likely unchanged).
- `--focus-ring` is `--stone` in light mode (user's decision): gold on parchment is ~2.2:1,
  under WCAG's 3:1. UI-2 picks the dark value. Gold on stone (~5.5:1) passes.
- `.btn-secondary`, `.field-help` and `.field-error` were deferred to the first task that uses
  them (UI-10, UI-9, UI-12). Two new tests forbid CSS classes that no template uses, and template
  classes `web.css` doesn't define, so defining them now would have meant unused CSS plus an
  allowlist.
- `portfolio_chart.js` still reads brand tokens directly. Switching it to `--gain`/`--loss`/
  `--border` stays with UI-3, as planned.

### UI-2. Dark palette proposal & brand sign-off

**Status:** done (signed off 2026-10-07)

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

**Outcome.** The scope grew during the task. Measuring the existing tokens showed three light-mode
AA failures: `--stone-600` on `--parchment-deep` at 4.14:1, gold as a focus ring on parchment at
2.22:1, and `--hairline` as an input border at about 1.3:1. They were fixed here and signed off
together with the dark set:
- **Light fixes:** `--stone-600` darkened to `#5F5E57`, plus new `--gold-deep` (focus ring only)
  and `--control-border`.
- **Dark set:** new `--stone-deep`, `--parchment-muted`, `--hairline-on-dark` and
  `--control-border-on-dark`. Cards use `--stone`, text uses `--parchment`, the focus ring is
  `--gold`.
- **Error treatment:** an *ink rule* (a 3px bar in the text color, an icon and a bold lead word),
  neither gold nor oxblood.

These are raw tokens only. The semantic aliases and the `[data-theme]` blocks are still UI-1 and
UI-3. Ratios are recorded in chapter II of the charter and enforced by
`services/web/tests/unit/test_brand_contrast.py`.

### UI-3. Dark mode

**Status:** done

Depends on UI-1 (semantic aliases to re-point) and UI-2 (signed-off values).

- `web.css` re-points its semantic aliases to UI-2's dark tokens under both
  `:root[data-theme="dark"]` (manual choice) and
  `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { … } }` (system
  default), and sets `color-scheme` so native form controls, scrollbars and the date picker
  follow. `brand/tokens.css` is unchanged: UI-2 already added the raw dark values.
- **Choice persisted in a cookie** (`stakehold_theme` = `light` | `dark` | `system`, default
  `system`). A new
  CSRF-checked `POST /theme` sets it and redirects back. `base.html` renders `data-theme` on
  `<html>` from the cookie **on the server**, so a manual choice applies with no flash of the
  wrong theme and no inline script. `system` renders no attribute and leaves it to the media
  query.
- A header toggle: a small form (light / dark / system) that works as a plain POST and is
  htmx-enhanced to switch without a full reload.
- The logo swaps between `logo-stone.svg` and `logo-gold.svg` by theme: both are rendered, and
  CSS shows one using the same selectors as the dark aliases. The mark itself is never recolored.
- `portfolio_chart.js` reads the semantic tokens (`--gain`, `--loss`, `--border`, `--text-muted`)
  rather than raw brand tokens, and redraws when the theme changes (a `matchMedia` listener plus
  the toggle's swap event).

Files: `static/web.css`, `templates/base.html`, `api/theme.py` (new), `api/app.py`,
`static/portfolio_chart.js`, `static/theme.js` (new), `docs/architecture.md`.

Tests: `POST /theme` (each valid value, invalid value rejected, CSRF required, cookie attributes);
`data-theme` rendered correctly per cookie value, including no cookie; hex-scan still green.

As built (decided with the user before implementation):
- **The dark blocks live in `web.css`, not `tokens.css`** as first written. UI-1 made the aliases
  the thing a theme re-points, and UI-2 had already added the raw dark tokens to `tokens.css`.
  The dark block is written twice (attribute selector and media query), so a new test asserts
  the copies are identical and re-point every color alias. The contrast test now covers the
  dark aliases too.
- **Logo via CSS**, not `<picture>` plus a server-chosen `<img>`. That approach would have needed
  JS to rebuild the element after an in-place switch.
- `theme` reaches every template through a Jinja2Templates context processor, so no route
  changed, and the 403 page (rendered by an app-level handler) follows the cookie too.
- The toggle is three submit buttons with `aria-pressed`. Its redirect target is a hidden
  `next`, validated as a same-site path. The htmx path gets an `HX-Trigger` instead of a
  redirect, and `theme.js` applies it.
- Route tests are in a new `tests/unit/test_theme.py`, since `test_routes.py` is near the 64KB
  file limit.
- Checked in Chromium (Playwright, fake upstreams): system light and dark, manual over the OS
  setting, the htmx switch without a reload, the no-JS post and redirect, the chart redrawing on
  both the toggle and an OS change, and no horizontal scroll at 360px. Under the strict CSP the
  console shows one blocked inline style per page load. It's also on `main`, so it predates
  UI-3 (probably htmx's indicator `<style>`); worth picking up with UI-12's `hx-indicator` work.

### UI-4. App shell & navigation

**Status:** done

Depends on UI-1. Today the nav has one "Portfolio" link that goes to `/accounts`, with no active
state; nothing ties an account's pages together.

- Header nav: **Overview** (`/`), **Accounts** (`/accounts`) and, for admins only, the existing
  **Admin** link (`/admin`), with an active state driven by a template variable each route sets.
- On account-scoped pages (`/portfolio`, `/transactions`, `/transactions/new`,
  `/transactions/import`): a shared account header with the account label and broker, a sub-nav
  (Holdings / Transactions / Add / Import), and an account switcher (a plain `<select>` +
  submit, htmx-enhanced) built from `list_accounts`.
- A skip-to-content link, a contained page width, and a header that collapses gracefully at
  phone width.
- A shared `partials/_account_header.html` so the four pages don't each re-implement it.

Done when: every page is reachable from the nav without typing a URL, and the current page is
visibly marked.

As built:
- The active state is `aria-current="page"`, rendered from `nav_active` / `account_nav_active`
  and styled off that attribute (gold underline plus weight), so there's no new class or script.
- **The Transactions sub-nav link ships now and 404s until UI-8** (user's decision), so the
  sub-nav doesn't change shape later.
- **Unknown accounts now 404** on `/transactions/new` and `/transactions/import`, as on
  `/portfolio`, via a shared `account_not_found.html` (user's decision). If `list_accounts`
  fails, the pages don't 404: they degrade to a label-only header with no switcher.
- **The switcher is a plain GET form with a "Switch" button, with no htmx enhancement.** An
  htmx body swap would skip `portfolio_chart.js`'s `DOMContentLoaded` start, leaving
  `/portfolio` without its chart. It is shown only when the user has two or more accounts.
- The header wraps at phone width, with the nav on its own row. No hamburger menu, so no JS.

### UI-5. Number & date formatting

**Status:** done

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

As built:
- **Dates are ISO (`2026-10-06`)**, not `6 Oct 2026` (user's decision). `date` also accepts a
  `datetime`, and the admin mappings page's `updated_at` now uses it too.
- **Zero is unsigned** (user's decision): `signed_money(0)` is `€0.00` and `pct(0)` is `0.00%`.
  The sign is chosen after half-up rounding, so `-0.001` also renders `€0.00`. `money` puts the
  `−` before the `€` (`−€1,234.50`).
- `qty` groups thousands (`1,200`) and is also used for the import confirmation's row count.
- The `portfolio.html` column headers lose their "(EUR)" suffix, since each figure now carries `€`.
  The template's gain/loss class tests (`>= 0`) are unchanged; UI-6 restyles that page anyway.

### UI-6. Account portfolio page redesign

**Status:** done

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

As built (decided with the user before implementation):
- **`list_latest_returns` is called on this page too**, though the latest row per ticker is also
  the chart history's last point: one extra concurrent request, built and tested here for UI-11.
  The summary is `aggregate_portfolio_returns`'s last point over those rows
  (`summarize_latest_returns`), so a closed ticker's dividends still count, and the percentage
  is recomputed, never averaged. It returns a `PortfolioReturnPoint`, not a new dataclass.
- **"Prices as of" is the newest `price_date`** across the latest rows, matching the chart's last
  point. A stale ticker isn't called out; that's UI-7's freshness work.
- **Weight** is a share of the summed market value of open holdings that have a return row. A
  holding with none yet shows `—` for its market value, weight and return (UI-7 adds the reason).
- **The market-value chart line uses `--text`**; total return keeps gain/loss segment colors.
  Ranges count back from the last data point, not today. Both control groups ship `hidden` and
  `portfolio_chart.js` reveals them, so a no-JS page shows no dead buttons.
- **Zero deltas are uncolored**, via a new `delta_class` filter that reads the sign after display
  rounding. A new `share` filter renders weights as an unsigned percentage. Tickers are mono now.
- The JS formatter mirrors `formatting.py` by hand, and no test ties the two together (there's no
  JS test runner). This is recorded as a sharp edge in `docs/architecture.md`.
- Route tests moved to a new `tests/unit/test_portfolio_page.py`, with its own fakes, because
  `test_routes.py` had grown past the 64KB limit (70KB, now 63KB).
- Checked in Chromium (Playwright, fake upstreams): light and dark at 1280 and 360 px, every
  range and both views, tooltip and axis formatting, the redraw on a theme switch, and controls
  hidden with JS off. At 360 px the holdings table scrolls inside its card rather than reflowing,
  which UI-13 owns.

### UI-7. Data freshness & rebuild feedback

**Status:** done

Depends on UI-6.

**Rescoped (2026-10-07).** This was "Refresh data from the UI": a manual Refresh button calling the
rebuild endpoints. Walking-skeleton task 18 made that unnecessary. `portfolio` now rebuilds
positions and returns after every write and every six hours, and the CLI wording is gone from the
empty states. What's left is making the automatic rebuilds visible, so a holding with no figures
never looks like a bug.

- **Rebuild outcome after a write.** `portfolio`'s `TransactionsWriteResponse` already reports
  `positions_rebuild` and `returns_rebuild` (`ok` / `partial` / `nothing_to_do` / `skipped` /
  `failed`), but `web`'s `TransactionWriteResult` drops both. Carry them through, and turn the
  "saved" / "imported" confirmations on `/transactions/new` and `/transactions/import` into a
  plain one-line outcome: for example "Saved. Holdings and returns updated.", or "Saved. Returns
  couldn't be updated; they'll catch up on the next scheduled refresh."
- **Pending states on the account page.** A holding with a position but no return row yet is
  labelled with the reason, not left blank. Either it is waiting for a ticker mapping (from
  `GET /instruments/unmapped`, which needs a new client method; an admin sees a link to
  `/admin/mappings`) or it is waiting for prices.
- **Freshness.** Say in plain words that prices refresh automatically. The "prices as of <date>"
  line itself stays in UI-6.

Tests: the write-result fields parsed by the client, including an older response missing them;
the confirmation message for each rebuild status; route and template tests for the two pending
states, and for the admin-only mapping link.

As built (decided with the user before implementation):
- **An unmapped instrument is a notice, not a holdings row.** It never becomes a position:
  `portfolio`'s positions rebuild drops it before it has a ticker, so there is no row to label.
  The notice sits under the Holdings heading, lists `kind identifier` in mono, and shows even with
  zero holdings. Admins get a "Map them" link to `/admin/mappings`; everyone else is told "An
  admin needs to map them."
- **Filtered by the account's broker**, because `GET /instruments/unmapped` is per user. This is
  a sharp edge: two accounts at the same broker each show the other's unmapped instruments.
  Recorded in `docs/architecture.md`, and the fix is listed under [Deferred](#deferred).
- **"Waiting for prices" is one state**, not split into "untracked ticker" and "not priced yet".
  It shows only when `/returns/latest` answered; if that call failed, the row keeps its `—`s.
- **The outcome travels in the redirect's query string** (`positions=`, `returns=`), like the
  admin mappings page's. It is turned into copy by a new pure `rebuild_outcome.py`, with
  unknown values ignored. `saved` is now the saved count, so a duplicate reference (`saved=0`)
  or a re-imported file (`imported=0`) says "Nothing new to save/import" instead of "saved". A
  failed positions rebuild never promises the scheduled refresh, which recomputes returns only.
- The freshness line names no interval ("Prices update daily; returns are recalculated a few
  times a day."), since the refresh period is `portfolio`'s config.
- Route tests are in a new `tests/unit/test_write_outcome.py`, because `test_routes.py` is at
  the 64KB limit.

### UI-8. Transactions list

**Status:** done

Depends on UI-4 and UI-5. There's no way to see what has been entered for an account, although
`portfolio` exposes `GET /transactions`.

- New `GET /transactions?account=`: a table of the account's transactions, newest first, showing
  date, type (as a neutral badge, never gain/loss colored), instrument, quantity, price, and
  amount + currency, plus an empty state linking to Add / Import.
- New `PortfolioClient.list_transactions` method and a `Transaction` dataclass mirroring
  `portfolio`'s `TransactionResponse`.
- Reached from the account sub-nav (UI-4), whose Transactions link already points to
  `/transactions?account=`. The route renders `partials/_account_header.html` with
  `account_nav_active="transactions"` and reuses `_account_header_context` for the
  unknown-account 404. Pagination is deferred unless the endpoint already supports it; check
  before building.

Tests: client method; route (login required, happy path, empty, unknown account → 404).

As built (decided with the user before implementation):
- **No pagination.** `portfolio`'s `GET /transactions` takes no paging, filter or sort
  parameters and returns the log oldest first, so `web` fetches it all and reverses it. It stays
  under [Deferred](#deferred), and `docs/architecture.md` records it as a known limitation.
- **Native figures get a new `native_money` filter** (user's decision): the currency code after
  the number (`−1,503.50 USD`), `money`'s `−`-only sign, never gain/loss colored. Prices use 4
  places with zeros past the second trimmed (`112.34 USD`).
- **Fees, Reference and the frozen EUR amount added beyond the spec** (user's decision). The
  EUR amount was asked for as its own column, but with nine columns the table overflowed the
  880px card at desktop width and squeezed Reference to two characters. So it's a muted second
  line under a non-EUR amount instead (none on a EUR row, where it would repeat the amount).
  A long reference (some brokers use 36-character UUIDs) is cut off at 14 characters with an
  ellipsis, with the full value still in the page and in a `title` tooltip. The table uses a
  13px figure size and tighter cells to fit.
- An ISIN or SYMBOL instrument is shown with its kind (`ISIN IE00…`), as UI-7's unmapped notice
  does; a TICKER is shown alone.
- The type badge is the charter's neutral ink tag (mono, uppercase, hairline border).
- **An outage isn't an empty account:** a failed `list_transactions` renders a banner and no
  table, never the "No transactions yet" state.
- The new table has its own `transactions-table` class and shares `.num` alignment with
  `holdings-table`, rather than renaming the portfolio page's class.
- Route tests are in a new `tests/unit/test_transactions_page.py`, because `test_routes.py` is at
  the 64KB limit.

### UI-9. Transaction entry form UX

**Status:** ready

Depends on UI-1. Partly done on main since this was planned: the instrument is now picked from
`data-store`'s tickers (a `<datalist>`), the identifier-kind field is gone, and buys and sells
derive the amount from price × quantity + fees. Still open: `/transactions/new` shows all its
fields at once regardless of type, requires a hand-typed reference, takes the currency as free
text, and shows the FX field even for EUR.

- Fields grouped into fieldsets: *What* (type), *When* (date), *Amount* (amount, currency, FX
  rate, fees), *Instrument* (ticker, quantity, price).
- A new `static/transaction_form.js` shows only the fields relevant to the selected type (for
  example instrument, quantity and price for BUY/SELL; no instrument for a deposit), and shows FX
  rate only when the currency isn't EUR. Hidden fields are cleared so they aren't submitted.
  Without JS every field is shown, exactly as today. Server-side validation is unchanged and stays
  the source of truth.
- Reference prefilled server-side with a generated, editable `manual-<uuid4>`. It stays editable
  because it is the idempotency key, so a user re-entering a known broker reference can still do so.
- Currency as a `<select>` (or input + datalist) of the currencies `data-store` supports,
  defaulting to EUR. Date defaults to now. Short help text on the less obvious fields (reference,
  FX rate direction, when the amount is derived).
- The type→fields mapping is confirmed against `transactions/base.py`'s `validate()` in
  `portfolio`, not guessed.

Tests: the generated reference is present and unique per render; the form still posts every
field without JS (template test); existing submit tests unchanged.

### UI-10. Accounts & import pages

**Status:** ready

Depends on UI-4.

- **Accounts**: the account list comes first as rows or cards, each with its broker and one
  primary action (open holdings) plus secondary links. "New account" moves to a secondary
  `<details>` disclosure, open automatically when the user has no accounts. A first-run empty
  state explains the steps: create an account, then add or import transactions.
- **Import**: a styled file drop zone (a real `<input type="file">` underneath, so it works without
  JS), the account and broker shown, the 20 MB limit stated, and after a successful import a
  result summary (with UI-7's rebuild outcome, already in the banner via `rebuild_outcome.py`)
  and next steps (view holdings, view transactions).

Tests: route/template tests for the empty-state disclosure behaviour and the import summary.

### UI-11. Home dashboard

**Status:** ready

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

**Status:** ready

Depends on UI-1 and UI-2 (the error treatment).

- Styled success and error banners (`partials/_form_errors.html` and the confirmation banners)
  using UI-2's error treatment. `#form-errors` becomes an `aria-live="polite"` region.
- htmx `hx-indicator` plus a disabled submit button while any form request is in flight, so a slow
  submit can't be double-posted.
- Branded 404 and 5xx pages (extending `base.html`, plain copy) instead of FastAPI's JSON or
  Starlette's default error pages, following the pattern of the existing 403 page
  (`forbidden.html`).
- Auth pages: centered card with the mark, a password show/hide toggle (external JS), and the
  12-character minimum stated up front on signup instead of only on failure.

Tests: exception-handler routes render the branded pages with the right status codes; the
`aria-live` attribute is present; existing htmx error-swap tests still pass.

### UI-13. Responsive & accessibility pass

**Status:** blocked

Last, once every page exists in its new form.

- Tables (holdings, transactions) reflow at phone width (stacked rows or a horizontally
  scrollable container with a visible cue). No page scrolls horizontally at 360 px.
- Contrast audit of every page in both themes against WCAG AA; fix any regressions. Token-level
  pairs are already enforced by `test_brand_contrast.py` (UI-2), so this audit is about how pages
  *use* the tokens (wrong token on a surface, opacity, text over images).
- Keyboard-only walkthrough of every flow (signup → account → add → view, and the admin
  mapping flow), with visible focus throughout.
- A Playwright screenshot script under `services/web/` (Playwright is already a workspace
  dependency, used by `data-store`) that captures every page in light and dark at desktop and
  phone width, for review rather than as a pixel-diff gate.

### UI-14. Broker dropdown & importer choice

**Status:** done

Depends on UI-4. Added after the chunk was planned. A broker with only an account-statement
(cashflow) parser couldn't be imported from `web` at all, because the import page only ever
called `portfolio`'s transactions route. And a broker typed in the wrong case at account creation
(`Acme` vs the installed `acme`) silently got no importer.

- **Accounts**: the broker field is a `<select>` of installed brokers plus "Custom" (a typed
  name, manual entry only). A custom name matching an installed broker ignoring case is refused.
- **Import**: an importer `<select>` lists the account's own broker's importers ("Trades export"
  → `POST /transactions/import`, "Account statement" → `POST /cashflows/import`). A broker with
  none points to manual entry instead of showing a form.
- **`portfolio`**: `GET /brokers` (and `portfolio broker list`) now reports each broker with its
  import kinds, read from both parser registries. That is a changed response shape on an existing
  endpoint, not a new endpoint, so it stays within this chunk's constraint.

Files: `portfolio`'s `pipeline.py` (`AvailableBroker`), `jobs.py`, `cli.py`, `api/{models,routes}.py`;
`web`'s `portfolio_client/{base,http_client}.py`, `api/routes.py`, `accounts.html`,
`transactions_import.html`, `web.css`; both services' `docs/architecture.md`.

UI-10 (accounts & import page redesign) builds on this form rather than the old free-text one.

## Deferred

Out of this chunk because each needs a new `portfolio` endpoint (and possibly a schema discussion
per `.claude/rules/data-schema.md`), which this chunk deliberately excludes:

- Editing or deleting a transaction.
- Renaming or deleting an account.
- A server-side cross-account return-over-time series (UI-11 shows the current snapshot only).
- Pagination or filtering of transactions, if `GET /transactions` doesn't already support it.
- Unmapped instruments per account, not per user (UI-7 filters by broker, so two accounts at one
  broker share a list).
