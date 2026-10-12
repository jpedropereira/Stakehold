# web UI architecture

The presentation half of [`architecture.md`](architecture.md): how pages look and behave in the
browser. That file keeps the server side (auth, CSRF, security headers, the upstream clients,
routes and the data they assemble); this one keeps the design system in use, the theme, the
chart, display formatting, page layout and the scripts that enhance forms. It's one document
split in two for size, so `.claude/rules/architecture.md` applies to both: a change to anything
described here updates this file in the same change.

## Key decisions

- **Ticker onboarding pages (TK-8, TK-9).**
  - **Status is a neutral badge** (the existing `.badge`, ink with a hairline): Queued,
    Loading, Ready, Couldn't load, plus an "Off" badge for a disabled ticker. Never gold or a
    gain/loss color, since loading isn't an action to take and a failure isn't a loss. The
    labels live once, in `ticker_status.py`.
  - **The lookup result tells the user what will happen before anything does.** An available
    symbol shows its name, exchange and currency (`mono`) and an "Add" button; the provider's
    spelling is what gets posted, not what was typed.
  - Every other outcome is one plain sentence in a warning banner, never an error: nothing
    went wrong, the symbol just can't be added. "Delisted securities may not be available" is
    said wherever a symbol isn't found.
  - **A minor-unit currency (`GBp`, `ZAc`, `ILA`) gets a warning before adding.** Nothing in
    Stakehold rescales those prices yet, and saying "its values will read 100 times too high"
    is better than a surprise on the holdings page.
  - **No new CSS**: the pages reuse cards, `.field`, `.positions-table`, `.badge`,
    `.inline-form`, `.field-hint` and the banner macro.
  - A holding without prices reads "Prices loading" or "Prices couldn't be loaded" in the same
    spanning cell that read "Waiting for prices" (see `architecture.md`).

- **Responsive and accessibility pass (UI-13).**
  - *Stacked tables*: at 600px and below, the holdings and transactions tables become one block
    per row. The first cell (the ticker or the date) heads the block, and each other cell is a
    "label, figure" line. The label is drawn from the cell's `data-label` by
    `content: attr(data-label) / ""`. The empty alt text is there because the column header,
    still in the page but visually hidden, already names the cell for a screen reader; without
    it, every label would be read twice. Changing a table's `display` drops its table semantics
    in WebKit, so the templates set explicit `table`/`rowgroup`/`row`/`columnheader`/`cell`
    roles, which cost nothing at desktop width. This won over scrolling with an edge cue
    because, at 360px, a scrolling eight-column table shows about three columns at a time, and
    the figures a holding is read for (value, return) would be the hidden ones.
  - *Admin tables still scroll*: the mappings tables are an admin's desk tool, with forms in
    their cells. Their `.table-scroll` box is a focusable, labelled region (`tabindex="0"`,
    `role="region"`), so a keyboard can scroll it in browsers that don't make scrollers
    focusable on their own. It gets the same focus ring as everything else.
  - *Date input focus*: Chrome's `datetime-local` has a calendar button inside it that is a
    Tab stop of its own. While that button has focus, the input matches `:focus-within` but not
    `:focus-visible`, so the field gets the ring for `:focus-within` too.
  - *Review script*: `scripts/review_pages.py` serves `create_app()` against in-process fake
    clients, then screenshots every page in both themes at 1280px and 360px. A page entry can
    name an element to click once loaded, for a state only a click reaches, such as the
    transaction log's open delete dialog. For each of those variants it also checks:
    - no sideways scroll at phone width;
    - WCAG AA contrast of every visible text element against the background actually behind
      it;
    - a visible focus indicator at every Tab stop, a date input's inner stops included;
    - no console errors.

    Fakes rather than the running stack, so it needs nothing up, shows the same data every run,
    and can't touch a dev database. It complements `test_brand_contrast.py`, which proves the
    alias pairs; the script proves how pages use them. The script is for a person to review,
    not a CI gate: it needs a Chromium download and takes about a minute, and screenshots would
    make a brittle pixel diff. Its own contrast check was chosen over axe-core to avoid a
    third-party dependency. It covers contrast and focus only, not labels or ARIA use. A unit
    test checks that its page list includes every `GET` page route, so a new page can't skip
    review. **Known limitation:** text drawn on the chart's canvas isn't measured. It's
    colored from the same aliases, which `test_brand_contrast.py` covers.
- **Feedback and error states (UI-12).**
  - *Banners*: every message is one macro, `partials/_banner.html`, in the charter's ink rule
    (chapter II): an icon and a bold lead word carry the meaning, never color, so errors use
    neither oxblood (a loss) nor gold ("act here"). Error and warning differ only by icon; a
    success has a hairline bar. The caller writes the content, `<strong>` lead first, so a lead
    can hold markup (the import count's mono figure). A form error's lead says which action
    failed and comes from `routes._ERROR_LEADS`, keyed by template, because
    `_render_form_error` is the one path every form error takes (CSRF failures included); the
    messages themselves are unchanged full sentences. `#form-errors` is an
    `aria-live="polite"` region, so an htmx-swapped error is announced.
  - *In flight*: each htmx form has `hx-disabled-elt="find button[type='submit']"` (htmx
    resolves `find` from the requesting form), so a slow submit can't be posted twice, and a
    `.htmx-indicator` busy line shown while htmx marks the form `htmx-request`. htmx 1.9 would
    inject those indicator rules as an inline `<style>`, which `style-src` blocks (the console
    error every page logged since htmx arrived); `base.html`'s `htmx-config` meta turns that
    off and `web.css` carries the rules. A no-JS post is unaffected: there is no double-post
    guard without JS, and the idempotency keys (a transaction's reference, an import's rows)
    are what make a repeat harmless.
  - *Unexpected failures*: `htmx-error-swap.js` still swaps only `X-Form-Error` responses, but
    any other failed htmx request (a 5xx, a branded error page, a lost connection) now puts a
    generic banner in the form's `#form-errors`, copied from a `<template>` in `base.html`, so
    the copy stays server-rendered and the page never looks like nothing happened.
  - *Error pages*: app-level handlers render `error.html` for Starlette's `HTTPException` (so
    routing's own 404 and 405 too; status and headers such as `Allow` kept),
    `RequestValidationError` (422) and any unhandled exception (500, logged as before). Like
    the 403 page, it's rendered without the user or a CSRF token: no nav, no theme toggle.
    Resolving the session would cost an auth call on every error, and could be what failed.
    The theme cookie still applies. `account_not_found.html` and `forbidden.html` stay
    separate, since they say something more specific.
  - *Auth pages*: the mark heads the login and signup cards (`partials/_logo.html`, shared with
    the header), and those pages empty `base.html`'s `header_mark` block so it isn't shown
    twice. Signup states the 12-character minimum (auth's own) up front.
    `password_toggle.js` reveals a show/hide button rendered `hidden`, so no-JS pages show no
    dead control.
- **The accounts page lists accounts first and folds the create form into a `<details>`
  (UI-10).** Creating an account is rare once there is one, so the form starts collapsed then.
  It renders `open` in two cases. The first is a first run (no accounts, and the list loaded),
  which also shows the two first steps. The second is any re-render carrying a create-form
  `error`: a no-JS post that fails lands back on this page, and the message sits inside the form.
  The list outage notice moved out of the form into its own `list_error`, above the list. As one
  shared `error`, it would have been hidden in the collapsed form, and its presence would have
  opened the form. A failed list is not a first run: no steps, form collapsed, notice shown. Each
  row's primary action is the account name itself, a link to its holdings. Transactions, Add and
  Import are `.btn-secondary` beside it. *Rejected: a gold `.btn-primary` per row*, since five
  accounts would mean five gold buttons competing with the one accent (charter, chapter II).
- **The import drop zone is the file input itself, enlarged and dashed; no script (UI-10).**
  Browsers accept a dropped file on a file input natively, and the input's own text names the
  chosen file, so a CSS-only zone keeps the strict CSP and works without JS. The cost: there's
  no highlight while a file is dragged over the zone. *Rejected: an invisible input stretched
  over a styled box*, which hides the chosen file's name, and a drag-state script, which is one
  more asset for a cosmetic gain. The stated limit comes from `routes._MAX_UPLOAD_MB`, the same
  constant behind the size check and its error message, so the copy can't drift from the cap.
  After an import, the summary (count plus rebuild outcome) links to the account's holdings and
  transactions, and the form stays below it as "Import another file".
- **Chart.js is vendored (`static/vendor/chart.min.js`), and the chart reads its colors from
  the semantic tokens at runtime rather than from a hardcoded hex value.** `script-src 'self'` (no CDN
  exception, no `unsafe-inline`, no nonce) rules out both a CDN `<script src>` and an inline
  `<script>` block with real logic — this codebase has never had one, and adding the first here
  would either be silently blocked by the browser or force a CSP weakening this task's own
  no-CDN framing argues against. The chart's numeric data is embedded via a non-executable
  `<script type="application/json">` data island in `account_holdings.html` (inspected as data, not run
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
  The island carries `market_value_eur`, `total_return_eur` and `growth_pct` per day, so
  switching between "Total return", "Market value" and "Growth %", or between 1M / 6M / 1Y / All,
  never makes a request. A range
  counts back from the *last data point's* date, not from today, so an account whose prices
  stopped updating still shows its final stretch rather than an empty chart. Total return is a
  delta, so its line keeps the gain/loss segment colors, and so does growth. Growth counts from
  inception, so a shorter range crops its line rather than restarting it at zero; a `null` (a
  date growth didn't cover) is a gap. The Growth % button is only rendered when growth loaded,
  and its tick and tooltip labels mirror `formatting.pct`. Market value is a level, so it's drawn in
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
  - *Logo*: both `logo-stone.svg` and `logo-gold.svg` are in the header (in the card on login
    and signup, UI-12), and CSS shows one,
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
- **App shell (UI-4): the current page is marked on the server, and the account pages share one
  header.** Each route passes `nav_active` (`overview` / `accounts` / `admin`) and, on the four
  account-scoped pages, `account_nav_active` (`holdings` / `transactions` / `add` / `import`). The templates turn these into `aria-current="page"`, and `web.css` styles
  that attribute rather than a class. The mark a screen reader announces and the one a sighted
  visitor sees therefore come from one source and can't disagree, and no script works out "where
  am I" from the URL. `partials/_account_header.html` (label, broker, sub-nav, switcher) is fed by
  `routes._account_header_context`, one `list_accounts` call. The same call answers "is this the
  visitor's account?": `/transactions/new` and `/transactions/import` used to render a form for
  any `?account=` label and leave `portfolio` to reject the write. They now 404 up front
  (`account_not_found.html`, shared with `/accounts/holdings`). The 404 happens only when the list loaded
  and the label isn't in it. If `portfolio` can't be reached, the page renders with a label-only
  header and no switcher, because an outage must not tell the visitor a real account doesn't
  exist. POST routes load the header lazily (`extra_context_loader`), so only a full-page error
  re-render pays for that call, never a successful write.
- **The account switcher is a plain GET form, with no htmx enhancement.** The form submits
  `?account=` back to the current path, so switching keeps the visitor on Holdings,
  Transactions, Add or Import. An htmx `hx-get` swapping the response into `<body>` was planned and rejected. htmx
  1.9 does take a full document's `<body>` and update the title, but page scripts that start on
  `DOMContentLoaded` (`portfolio_chart.js`) never run after a swap, so `/accounts/holdings` would lose its
  chart. A full navigation is also what an account switch is: every figure on the page changes.
  The cost is a visible "Switch" button, since the form can't submit on `change` without a script.
  The Transactions sub-nav link shipped ahead of its page (UI-4) and 404ed until UI-8 built
  `GET /transactions`.
- **The manual-entry form shows only the fields a type uses, and the mapping lives on the
  server (UI-9).** `routes.py`'s `_FIELD_TYPES` / `_FIELD_REQUIRED_FOR` say which types each
  type-dependent field applies to and requires. They mirror `portfolio`'s `validate()` and
  `_manual_amount`, with a comment there, the same way `_TRANSACTION_TYPES` mirrors its type
  list. The template renders them as `data-types` / `data-required-for` attributes, and
  `static/transaction_form.js` only reads them, so the mapping isn't duplicated in JS. A hidden
  field's controls are *disabled*, not cleared: a disabled control isn't submitted, and a value
  typed before a type switch comes back on the way back. The server renders the form with no
  field hidden and no type-dependent `required`, so without JS it is today's form, and
  `portfolio`'s validation stays the only gate either way. The amount is hidden for BUY/SELL,
  because `portfolio` derives it and accepts a typed one only if it matches. The reference is
  prefilled with a fresh `manual-<uuid4>` per render. It's the idempotency key, so a
  double-submitted form saves once, and it stays editable for a known broker reference. The
  date defaults to now (UTC) through a `_utcnow()` seam for tests. **Known limitation:** the
  currency suggestions (`_SUGGESTED_CURRENCIES`) are a constant in `web`, because nothing
  exposes `data-store`'s `currencies.yaml` and this chunk adds no endpoints. The field still
  accepts any code, so drift only makes the suggestions less useful. The fix, an endpoint
  listing supported currencies, is under Deferred in `tasks.md`.
- **A delete is confirmed in a native `<dialog>`, with a confirmation page behind it.** Each row
  of the transaction log has a Delete link to `GET /transactions/delete`, and each account on
  the accounts list one to `GET /accounts/delete`: a full page with what is about to go, the
  warning and the form. Without JS that page is the whole flow.
  `static/confirm_dialog.js` intercepts a plain click on any `[data-confirm]` link and opens the
  dialog the link names instead: it copies the link's query string into the dialog form's hidden
  fields (the page's form posts the same names, so there is no second mapping to keep in step)
  and the link's `data-confirm-summary` into the summary line as text, since it comes from broker
  data. A modified click still opens the page in a new tab. One dialog per page
  (`partials/_delete_dialog.html`), not one per row, and every word of it is in the template.
  The native dialog gives the modal behaviour without code: focus stays inside it, Esc closes it,
  and the page behind is inert. Cancel is a `formmethod="dialog"` button, which closes it and
  posts nothing; the script only returns focus to the link that opened it and disables the
  confirm button while the delete posts. Cancel comes first and takes the focus on open, so an
  Enter pressed out of habit doesn't delete. The copy follows chapter IV: "Delete this
  transaction?" (or account), "This can't be undone." and what happens next, then "Delete
  permanently" and "Cancel". An account's summary is a sentence, "This deletes ‹label› and its N
  transactions.", so the visitor sees the size of what goes before confirming. The accounts list
  counts each account's transactions to say so (`transaction_counts.py`); a count that couldn't
  be loaded is worded "every transaction in it", never as zero. *Rejected: a summary with no
  count*, cheaper but the one figure that tells a visitor they clicked the wrong account. Ink only: the confirm button is `.btn-secondary`, never the gold `.btn-primary`
  (gold means "act here", chapter II) and never oxblood (a loss); the warning is the existing
  `banner("warning")`, and the backdrop is the dark page color (`--backdrop`) in both themes. The
  transaction's Delete link sits in its own narrow last column with no stacked label at phone
  width, since it names its row for screen readers itself; an account's is a plain link after
  Import, so the outlined buttons stay the row's everyday actions. *Rejected: a `confirm()` prompt*, which can't be
  styled or worded beyond one string and is suppressed by some browsers, and *a delete button
  per row posting straight away*, which is one slip from a loss that can't be undone.
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
