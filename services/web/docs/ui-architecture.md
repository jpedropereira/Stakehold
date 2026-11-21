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
    clients, then screenshots every page in both themes at five widths: phone 360px, tablet
    768px, desktop 1280px, laptop 1440px and wide 1920px (LY-1). Laptop came with LY-4: it sits
    just under the split breakpoint (1480px), so the stacked side of it is reviewed as well as
    the two-column side. A page entry can name an element to click
    once loaded, for a state only a click reaches, such as the transaction log's open delete
    dialog, or each chart view (total return, the default, plus market value, on all
    three chart pages). `--only` and `--width` narrow a run to some pages or widths. For each variant it
    also checks:
    - no sideways scroll, at every width: PF-4's table scrolled the page at tablet width for a
      whole task because the check only ran at phone width;
    - no table reaching past its card's content edge, unless it sits in a `.table-scroll` box,
      which is meant to scroll. A table that spills over its card doesn't always scroll the
      page (Transactions at 1280px didn't), so the first check alone misses it;
    - no `.table-scroll` box that scrolls while its `.split` shows two columns (LY-4). The
      breakpoint is chosen so the table column fits its table, and the check above skips
      anything in a `.table-scroll` box, so a breakpoint set too low would otherwise go unseen;
    - every chart canvas filling its `.chart-frame` (within 1px), 280 to 560px tall: 280 to
      480px stacked (LY-3), 320 to 560px in a `.split` column (LY-4).
      Comparing canvas to frame is the only check on the chart script and the CSS together,
      since there's no JS test runner. Without `maintainAspectRatio: false` the canvas falls back
      to 2:1 of its width, which at phone width is 144px inside a 280px frame. Where 2:1 of the
      width already lands inside the bounds (a frame about 560–960px wide), the two agree and the check
      can't tell;
    - every summary tile's figure on one line, inside its tile's content box. At a 160px tile
      minimum, `+€1,450.78` broke after its sign at phone and tablet, and unsigned six-digit
      figures ran up to 18px into the tile's padding. Neither scrolls anything or leaves the
      tile, so the overflow checks above couldn't see them;
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

    *Baseline before the screen-layout chunk* (LY-1, 2026-10-12): 180 of 192 variants pass.
    The 12 failures are Transactions and its open delete dialog, both themes:
    - tablet: the 844px table runs past its 656px card, and the page scrolls sideways;
    - desktop and wide: the same table runs past its 816px card. It stays inside the page.

    The screenshots show the rest. At 1920px every data page is an 880px column with about 520px
    empty on each side (`/portfolio`, about 1070px wide, leaves about 425px), and the logo
    sits left of the cards.
    At 768px the account-holdings page's five tiles wrap four and one. LY-2 to LY-7 are
    compared against this. After LY-3 added the two market-value pages: 196 of 208, the same 12
    failures. After LY-2: 208 of 208. Transactions fits its full-width card from about 1150px
    and scrolls in its box below that; the logo lines up with the content edge. After LY-4,
    which added the laptop width: 260 of 260. `/portfolio` is two columns at 1920px and
    stacked at 1440px and below. After LY-5: 260 of 260. Account holdings is two columns at
    1280px and up, stacked at tablet and phone. After LY-6, which added Overview's two chart
    views: 280 of 280. Overview is two columns at desktop, laptop and wide, and stacked at
    tablet and phone. The tile-figure check (below) then came in with its fix: 280 of 280.
- **A summary tile is sized by its headline figure.** The figure is 22px mono, about 13px a
  character, and never splits across lines (`white-space: nowrap`): "+" alone above "€1,450.78"
  reads as two values. The tiles' grid minimum is 200px, 152px inside the padding, which holds
  11 characters ("+€14,206.48"). It was 160px, chosen so the account page's five tiles fit one
  row at 880px; that held only 8 characters. The cost: a phone shows one tile per row rather
  than two by two, and tablet fits three a row. From desktop width the full-width pages still
  fit five on one row. Shrinking the figure to fit was rejected, as the headline size
  is where the numbers do the talking. **Known limitation:** a figure past 11 characters (a
  seven-digit total, `€1,234,567.89`) still runs into the padding on the narrowest tiles;
  `review_pages.py` would flag it only if the fake data had one.
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
  `<script type="application/json">` data island in `account_holdings.html` (and, since PF-6, `portfolio.html`) (inspected as data, not run
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
  Since LB-8 the color reading and that redraw live in `static/chart_theme.js`, loaded before
  each chart script, so `/labs`' chart shares them with this one rather than copying them.
- **The chart's view toggle and date ranges (UI-6) work client-side over the one data island.**
  The island carries `market_value_eur` and `total_return_eur` per day, so switching between
  "Total return" and "Market value", or between 1M / 6M / 1Y / All, never makes a request. A
  range counts back from the *last data point's* date, not from today, so an account whose prices
  stopped updating still shows its final stretch rather than an empty chart. Total return is a
  delta, so its line keeps the gain/loss segment colors. (A third view, "Growth %", time-weighted
  growth since inception, was removed along with its tiles and column; that kind of analysis is
  left to a future backtesting module.) Market value is a level, so it's drawn in
  `--text`, because forest and oxblood mean gains and losses only, and gold is kept for controls.
  The buttons are in the template, not built by the script: one macro,
  `partials/_returns_chart.html`, shared by the three chart pages, with the frame, island and
  scripts, so the pages can't drift apart (it went in with LY-6, which would have made a third
  copy). Each page keeps its own error and empty states around it. The buttons ship `hidden`, because without JS
  they'd do nothing, and the script reveals them. Building them in JS would also put their classes
  outside `test_templates.py`'s template/CSS class check. Only the first draw animates: morphing
  one range into another, with a different point count, draws stray lines. Tooltip and y-axis
  labels use a small formatter in `portfolio_chart.js` that mirrors `formatting.py`'s
  `money`/`signed_money`. **Known sharp edge:** that is two copies of one convention, and no test
  ties them together (there's no JS test runner). A change to the convention must touch both.
  Since UX-5 the buttons are the segmented control below.
- **Every view switch is one segmented control (UX-5).** Period, window, series and sort
  switches had been `.btn-ghost` text, underlined like links, the selected one only losing its
  underline. They read as links in prose, not as a control. `partials/_segmented.html` now
  draws them all as one bordered row of options:
  - *Two macros, one look.* `segmented_links` for the switches the server answers (Labs' growth
    period and comparison window, Risk's ex-ante window and ranking order): plain links, so
    they work without JS as before, the current one `aria-current="true"`. It takes the routes'
    existing `{value: url}` link dicts unchanged. `segmented_buttons` for the returns chart's
    client-side view and range: `aria-pressed`, flipped by `portfolio_chart.js`, which needed no
    change.
  - *A labelled `role="group"`, not a `<nav>`.* A view switch isn't site navigation, and `/risk`
    would otherwise gain a landmark per switch.
  - *The selected state doesn't rest on colour.* A filled option, bold, with a gold bar along its
    foot: the mark the site nav gives `aria-current`. Gold stays a control colour, never gain or
    loss. Periods and windows are set in mono, like every number.
  - *It wraps rather than overflow.* Labs' seven growth periods must fit a 360px phone.
  - *Not the header's theme switch*, which UX-4 moves into the account menu; it may adopt the
    buttons variant there. UX-10 adds its `hx-*` attributes and section anchor in the
    `segmented_links` macro, so every server-answered switch gets them at once.
- **The chart's height comes from a `.chart-frame` around the canvas, not from its width (LY-3).**
  Chart.js's default 2:1 aspect ratio ties height to width: about 150px at phone width, and about
  775px once a chart runs the full content width. `portfolio_chart.js` turns that off
  (`maintainAspectRatio: false`) and the frame sets the height: `aspect-ratio: 2 / 1` of the
  frame's own width, held between 280px and 480px. On the 880px card of the time that was the
  height the chart always had (408px). The roadmap first asked for a `clamp()` on the viewport width; the frame's
  width was chosen instead, because from LY-4 a chart in a column is much narrower than the window,
  and a viewport-based height would put a 420px-wide chart at the 480px cap. Chart.js sizes the
  canvas from its parent, so the frame is `position: relative` and holds only the canvas. **Sharp
  edge:** the frame needs its explicit `width: 100%`. Left `auto`, `aspect-ratio` carries the
  bounds across to the width: `min-height: 280px` became a 560px minimum width and every phone
  page with a chart scrolled sideways, and `max-height` capped `/portfolio`'s chart at 960px wide
  inside a 1008px card. A layout that wants another height overrides `aspect-ratio` and the
  bounds on the frame, and moves `review_pages.py`'s `CHART_HEIGHT` with them; `.split` does
  (LY-4, below). At about 420px wide (a `.split` column's minimum) and at phone width, the x-axis
  ticks thin out on their own (`autoSkipPadding`) and the view and range buttons wrap, so neither
  needed changing.
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
  - *Toggle*: a form with three submit buttons (`name="theme"`), the current one marked
    `aria-pressed="true"`. Logged in, it sits in the header's account menu (UX-4, below);
    logged out (login, signup, an anonymous 404), it sits bare in the header, since there is no
    user to name a menu after. One `theme_toggle()` macro in `base.html` renders both. It's
    shown on every page rendered with a CSRF token; the 403 page has no token, so no toggle. Without JS it posts and gets a 303 back to
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
- **Page width: one content width, three tiers, set per card (LY-2, LY-7).**
  - *Content width*: `--content-max` is the content's own width, 1600px, with the side gutter
    outside it. Up to 1600px the content grows with the window; beyond it, it centres (about
    160px each side on a 1920px screen). It used to be 1120px *including* the page's padding,
    and the header padded 32px against the page's 24px, each in its own 1120px box, so on a wide
    screen the logo sat about 110px left of the cards. Now `.site-header-inner` and `.page` both
    take `width: min(100% - 2 * var(--gutter), var(--content-max))`, and the logo is on the
    content edge at every width.
  - *Gutter*: `--gutter: clamp(24px, 3vw, 48px)`, so a laptop gets more than 24px but the table
    doesn't lose much width to it. At phone width both the header and the page set it to 12px.
    That's a custom property on those two elements, not a second `:root`, because
    `test_templates.py` holds `:root` to one declaration block for the theme aliases.
  - *Tiers*: narrow (`.card-narrow`, 380px: auth, errors, the first-run card), form (`.card`,
    560px) and full (`.card-wide`, `max-width: none`, the content width). The account header,
    as-of line and tiles are always the content width. Full is the page's default, and a card
    opts into it: a full-width table sits in a `.card-wide` card, and form and narrow cards keep
    their own width on the same page (the admin mappings form). In a `.split` column a form card
    fills its column (Labs' editor, UX-8) and keeps the 560px cap only when stacked.
  - *No per-page switch (LY-7)*: from LY-2 to LY-6 a page opted into full width through
    `base.html`'s `page_width` block, which added `page-full` to `<main>` and lifted a
    `--page-wide` variable from its 880px default, the old single column. That kept the holdings
    pages and Overview on 880px until their two-column layouts landed, rather than turning them
    into long stacked pages with a 1600px chart. Once every data page had opted in, the switch
    had nothing left to switch, and the one thing still on 880px was the account header on the
    form pages (Add, Import, the two delete confirmations), whose sub-nav moved when the visitor
    went between those tabs and Holdings or Transactions. The variable, the class and the block
    went, and the header is the content width on every account page.
  - *Form cards under the account header start at its left edge*: `.page` is a centred flex
    column, so a 560px card under a 1600px header would float in the middle of the page, under
    nothing. `.account-header ~ .card` aligns it to the start instead. It's a sibling selector,
    so it reaches only cards placed directly after the header; `test_responsive_a11y.py` checks
    the four form templates still do. Full-width cards fill the row, so the rule changes nothing
    on Holdings or Transactions, and pages without an account header (Accounts, auth, errors,
    Tickers' add form) stay centred. *Rejected:* the card centred under the full-width header,
    and a narrower header tier for the form pages, which would have kept 880px alive.
  - *Breakpoints* are listed once, by name, in a comment by `--content-max`, because a custom
    property can't be used in `@media`; each media query's comment cites the name.
  - *Transactions* gets the spare width in its text columns: above the stack breakpoint the
    figures, date and Delete cells ask for `width: 1%`, which in an auto-layout table means "only
    your content's width". The broker reference now shows whole; only a stacked row still cuts it at 24ch.
    With a 36-character UUID the table is about 1020px, so it fits its card from a window of
    about 1150px. Between the stack breakpoint and that, the nine columns don't fit, so the table scrolls in a focusable
    `.table-scroll` region, as `/portfolio`'s does. **Sharp edge, fixed here:** `.table-scroll`
    is `position: relative`. Without it, the visually-hidden "Actions" header, which is
    absolutely positioned, took the page as its containing block, escaped the box's clipping,
    and still scrolled the page sideways at 768px.
- **A holdings table and its chart share a row on a wide screen: `.split` (LY-4).** On
  `/portfolio` the as-of line and tiles span the page, and below them a `.split` grid holds the
  Holdings card and the Returns card. Account holdings reuses it with a modifier,
  `.split-account` (LY-5, below), and Overview with another, `.split-overview` (LY-6, after
  this entry). Labs puts its mix editor beside its returns table with `.split-labs` (UX-8,
  breakpoint `labs-split`; its arithmetic is in [`labs-ui.md`](labs-ui.md)): the editor first,
  as the page's main action, and a form card in the narrow column.
  - *Grid, not markup order*: the cards are in the markup in reading order, tiles then table
    then chart, and the grid places the columns. A screen reader or keyboard meets the page in
    the same order at every width, which moving the chart's markup to put it first would
    break.
  - *Breakpoint, measured*: the ten-column table is 875px at its natural width, plus 64px of
    card padding, so the table column's minimum is 940px. The chart's is 420px. With the 24px
    gap that's 1384px of content, a window of about 1473px once the 3vw gutters are taken off;
    the split breakpoint is 1480px, a few pixels of slack. Wider, the spare width splits 3:2:
    at 1920px the columns are about 946px and 630px. Below 1480px the cards stack at full
    width, as before, so a 1280–1440px laptop gets a 1200–1350px table card rather than the old
    880px one. All ten columns stay (agreed requirement). Measured with PF-6's Growth % column,
    since removed: with nine columns the table is narrower, so the breakpoint has more slack than
    stated here (not re-measured). The table's width depends on the data
    (longer figures, a longer ticker); if real data outgrows the 940px minimum, the table
    scrolls in its `.table-scroll` box rather than the page, and `review_pages.py` flags it
    for the fake data.
  - *Stacked column is explicit (sharp edge, fixed here)*: `.split` sets
    `grid-template-columns: minmax(0, 1fr)` even when stacked. Left `auto`, the implicit
    column grew to the card's min-content width, which counts the 875px table inside its
    `.table-scroll` box, and every phone and tablet `/portfolio` page scrolled sideways. As a
    flex item with `width: 100%`, which is what the cards were before, that didn't happen.
  - *Top-aligned, the chart sticky*: opening a holding's breakdown makes the table much taller
    than the chart. Stretching the Returns card to the row would leave a tall empty card, or a
    very tall chart, so both cards align to the top and the Returns card (`.split-aside`) is
    `position: sticky`, staying in view while the table scrolls past. Only in a window at least
    640px tall (the "tall" breakpoint): the card is about 580px at most, and in a shorter window
    a pinned card would hide its own bottom until the table ran out. The site header isn't
    sticky, so the card needs no offset for it.
  - *Chart 4:3 in the column*: the roadmap first said the chart would "stretch to its row",
    which contradicts top alignment. In a 420–630px column, LY-3's 2:1 would make it 210–315px
    tall, so inside `.split` the frame is `aspect-ratio: 4 / 3`, held between 320px and 560px:
    about 470px at 1920px. Still from its own width, as LY-3 chose, never from the viewport or
    the table. `review_pages.py`'s `CHART_HEIGHT` moved to 280–560px to cover both layouts.
  - *Account holdings, `.split-account` (LY-5)*: the account page's table has seven cells, not
    ten, and is 640px at its natural width (measured on the review data, whose "Prices loading"
    row spans four cells), so its column's minimum is 704px with padding. With the gap and the
    chart's 420px that is 1148px of content, a window of about 1221px. The `split-account`
    breakpoint is 1240px: the extra slack covers a classic 15px scrollbar, which `vw` gutters
    count but the content box doesn't (headless Chromium has none, so the review can't see it).
    A 1280px laptop gets two columns, about 708px and 472px; 1920px gets the same 3:2 as
    `/portfolio`. The account header, as-of line and tiles span both columns. The table needs no `.table-scroll` box: stacked, a full-width card fits
    it from well before the stack breakpoint.
    *A modifier, not new values on `.split`*: the container is `split split-account`, and the
    modifier's rules come after `.split`'s, so from 1480px, where both apply, its 704px minimum
    wins over `/portfolio`'s 940px. The chart frame's 4:3 and the sticky aside are repeated in
    the modifier's own queries, because a breakpoint can't be a custom property, so each
    breakpoint needs its own `@media` block. Folding the shared declarations into a
    custom-property scheme would have rewritten LY-4's rules, which LY-6 then reused.
    The chart card is 470–580px tall here, so the `tall` breakpoint still holds.
- **Overview's accounts list beside its returns chart: `.split-overview` (LY-6).** Overview
  (`/`) is a full page: the as-of line, tiles and the "Totals leave out" warning span it, and
  below them `.split` holds the Accounts card then the Returns card, accounts first in the
  markup. The chart is `/portfolio`'s, from the same `GET /returns/portfolio` call, made
  alongside the account list, so it needs no portfolio change.
  - *Its own columns and breakpoint*: `/portfolio`'s 940px table minimum and 1480px breakpoint
    would keep a short list stacked on every laptop. The modifier gives the chart the wider
    column (2:3), since it is the page's one figure. The accounts column's minimum keeps an
    account row on one line: 386px for the longest row in the review data, 450px with the card's
    padding. With the chart's 420px and the 24px gap that is 894px of content, a window of
    about 951px, or 967px with a classic 15px scrollbar (as LY-5 found); the "overview-split"
    breakpoint is 980px. Rows wrap (`flex-wrap`), so a longer label or larger figures wrap the
    row, never the page. As with `.split-account`, the modifier's rules come after `.split`'s,
    so they win from 1480px, where both apply.
  - *No sticky chart*: the list is shorter than the chart, so `.split-aside` would pin nothing.
    The chart frame is the same 4:3, 320–560px as in `/portfolio`'s column.
  - *Each card owns its failure*: an account-list failure is an error banner inside the
    Accounts card and the chart still draws; a returns failure is a banner inside the Returns
    card and the list and tiles still show. Before LY-6 an account-list failure was a banner
    alone on the page. The first run (the list loaded and empty) is still the narrow welcome
    card alone, with no chart.
- **App shell (UI-4): the current page is marked on the server, and the account pages share one
  header.** Each route passes `nav_active` (`overview` / `portfolio` / `labs` / `risk` / `accounts` / `tickers` / `admin`) and, on the four
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
  re-render pays for that call, never a successful write. The Risk sub-pages (UX-6) share
  `partials/_risk_header.html` the same way: `risk_nav_active` marks the current sub-page, and
  `.risk-subnav` takes the account sub-nav's styles. See [`risk-ui.md`](risk-ui.md).
- **Site header (UX-4): six sections in the nav, the rest in an account menu.** The header
  had grown to seven nav links, the user's name, Log out and the three theme buttons on one
  row. The main nav now holds only the sections: Overview, Portfolio, Labs, Risk, Accounts and
  Tickers. Admin, the theme switch and Log out sit in a menu opened from the user's name.
  - *Admin left the nav*: only admins see it, and it's a tool rather than a section of the
    user's portfolio. On an admin page the menu's Admin link carries `aria-current="page"` and
    no nav link does.
  - *A `<details>`, not a scripted dropdown*: it opens and closes without JS, and the browser
    exposes its expanded state. Its contents are ordinary links and forms, so it takes no ARIA
    `menu` roles, which promise arrow-key navigation that a disclosure doesn't need.
  - *`static/account_menu.js` adds the other ways out*: Escape (focus returns to the name), a
    click outside, and focus leaving the menu. A click inside, such as a theme button, leaves
    it open, so the theme change shows in place.
  - *The panel floats*, right-aligned under the name and capped at the viewport less the
    gutters, so opening it neither makes the header taller nor pushes a phone-width page
    sideways. At phone width the mark and the name share the first row and the six sections
    fill the second, spread with `space-between` to fit one line at 360px.
- **`/portfolio`'s per-account breakdown is a row of its own under each holding, opened from
  the ticker (PF-4).** One cell spans the table and holds a small table of the accounts. A row of
  its own, rather than a `<details>` in the ticker cell, gives the accounts the table's full
  width on desktop; at phone width the main row stacks as the per-account table does, the
  breakdown cell drops the stacked "label  figure" layout, and its inner table stacks too, one
  block per account. Every holding has one, single-account ones included, since it says which
  account holds it and links to that account's holdings. It used to be a `<details>` in that row,
  opened with no script, but its "In N accounts" summary repeated under every holding and cost a
  line each. The ticker is now a button (`aria-expanded`, `aria-controls` naming the row) and
  `holdings_breakdown.js` toggles the row's `hidden`: a control in one row can't open a
  `<details>` in another without script. The button ships `disabled` and every row open, so
  without JS each breakdown is shown and no button does nothing, the password toggle's
  convention. A CSS `:target` link was rejected: one open at a time, no way to close it, a scroll
  jump and no expanded state. The holding and breakdown drop the rule between them only while
  the breakdown is open (`:has()`), so a closed one leaves each holding its own rule. Native
  figures sit on a muted line under the EUR ones and only off EUR, where they'd repeat the EUR
  figure; a ticker recorded in different currencies reads "Mixed currencies" there, and the
  breakdown shows each account's own. Weight is over priced holdings only, the per-account
  page's rule: a holding waiting for prices isn't counted as worth zero. With PF-6's Growth %
  the table had ten columns, about 875px wide even with the transactions table's tighter cells
  and 13px figures, more than the 880px card holds inside its padding. Growth % has since been
  removed, leaving nine, but nine already overflowed before PF-6 (below), so the layout stays. So `/portfolio` takes the
  full content width (since LY-2; before it, `.content-wide` on its as-of line, tiles and
  cards), and the table sits in a focusable `.table-scroll` region. Stacked,
  between about 600px, where it stacks, and about 1000px, where it fits a full-width card, it
  scrolls in its box. In two columns (LY-4, from 1480px) its column never goes below the
  table's width, so the box is only a scroller below 1000px. Before that box, PF-4's nine columns already made the whole page
  scroll sideways at tablet widths, which `review_pages.py`, checking sideways scroll at phone width only at the time, didn't see
  (it checks every width since LY-1). Adding Portfolio made five nav links for an admin, so the phone nav's gap tightened
  and it wraps rather than scrolls on anything narrower than 360px.
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
- **Glossary: one page, linked from each figure's label.** The holdings tables show two
  percentages side by side (unrealized return and total return; a third, growth, has since been
  removed), and nothing on the page said how they differ. `/glossary` defines every figure the holdings pages and the overview
  show, and (LB-6) the Labs terms and (RK-12) the risk measures and (RK-14) the benchmark and its four measures, one `<dt id>` per term. Each column header and tile label on `/`, `/portfolio` and
  `/accounts/holdings` links to its entry through the `term` macro
  (`partials/_term.html`). The link inherits the label's colour with a dotted underline, so a
  row of headers doesn't turn into a row of accent links. At phone width a stacked table hides
  its `<thead>`, and its `data-label`s are CSS text that can't be links. So each holdings card,
  and the overview's accounts card, whose rows have no headers, also carry one plain "What these
  figures mean" link. The chart's view buttons aren't linked, since a link inside a button is
  invalid. *Rejected: `title` tooltips*, which don't show on touch screens and which screen
  readers announce inconsistently. *Rejected: an explanation under each table*, which would be
  three copies to keep in step. *Known cost:* an anchor renamed in `glossary.html` breaks its
  links silently in a browser. Each page's tests fetch `/glossary` and check every link lands
  on an entry.
- **Ticker names (UX-1): a tooltip that is also text.** A bare `VWCE.DE` doesn't tell everyone
  what it is. The `ticker` macro (`partials/_ticker.html`) takes the page's name map from
  `ticker_names.load_names`. The name sits in the markup after the ticker, clipped like
  `.visually-hidden` until shown, so a screen reader reads "VWCE.DE, Vanguard FTSE All-World"
  without opening anything.
  - **Opening it.** It opens on hover and on `:focus`, not `:focus-visible`: a tap focuses the
    ticker without matching `:focus-visible`, and the tap is how touch opens it. The focus ring
    stays on `:focus-visible`. `ticker_tooltip.js` lets Escape close it in place (WCAG 1.4.13);
    without JS, moving away closes it.
  - **No name.** A ticker without one (untracked, ISIN-only, or the lookup failed) is plain mono
    text, with no tab stop and no empty tooltip.
  - **Inside a link, button or label.** `focusable=false` drops the ticker's own tab stop there,
    since a nested one is invalid. The host's hover and focus open the tooltip instead. Focus
    is `:focus-within` on the host: a label's focus is on the box inside it, never on the label
    (UX-3).
  - **Positioning.** Most ticker tables sit in a `.table-scroll` box, which clips anything
    absolutely positioned inside it. So the open tooltip is `position: fixed`, anchored to its
    ticker with CSS anchor positioning (Baseline since January 2026), and flips to whichever
    side keeps it on screen. It is out of flow, so it never widens a column. Only the open
    ticker carries the anchor name; one name shared by every ticker would anchor each tooltip
    to the last one. The tooltip uses the page colour (`--surface`), so it stands off the card
    its ticker sits on.
  - *Rejected: `title` tooltips*, for the glossary's reasons. *Rejected: a plain absolute
    tooltip*, clipped by `.table-scroll`.
  - *Known costs:*
    - Every named ticker is a tab stop.
    - A browser without anchor positioning falls back to the absolute tooltip, which those
      boxes clip.
    - With one ticker hovered and another focused, only one tooltip anchors correctly.
  - **Where it applies (UX-2).** Every ticker a page prints in a table, a sentence or a heading:
    holdings, transactions (a `TICKER` identifier only; an ISIN or broker symbol has no tracked
    name), Labs and Risk.
    - *Already named, left alone:* the Tickers list (its own Name column), and Risk's ranking
      rows and their "Left out" list, which print the candidate's name beside it.
    - *Not named:* a fund constituent such as `AAPL` inside an ETF. Only tracked tickers have a
      `display_name`; look-through data has a constituent's name, but nothing reads it for this.
    - *A sentence that repeats a ticker* names it at its first mention only, as in the regime
      notes, since each named ticker is a tab stop. That kept /risk's tab walk in reach of
      `review_pages.py`, whose cap went from 120 to 200 stops all the same.
    - `aria-label`s keep the bare ticker: markup can't go in an attribute.
  - **In charts (UX-3).** A canvas can't hold the macro, so a chart is named around it and in
    its tooltip. The Labs growth chart's "Assets on the chart" labels use the macro and its
    tooltip prints `TICKER — Name` (`labs-ui.md`). Risk's rolling correlation names its pair
    in the note and the selects above the chart (`risk-ui.md`). The drawdown chart and the
    rolling volatility and Sharpe charts draw the mix or the portfolio, so they name no ticker.
  - **Pickers (UX-2).** A `<datalist>` option can't hold the tooltip, so `ticker_option` puts the
    name in its `label`: `VWCE.DE — Vanguard FTSE All-World`. The value stays the bare ticker,
    so that is what the form submits. The label repeats the ticker because Firefox shows only the
    label, while Chromium and Safari show the value beside it. *Known cost:* Chromium shows the
    ticker twice. Used by the Labs and Risk benchmark fields, the Labs mix inputs and the Risk
    what-if candidate; the new-transaction and admin-mapping pickers stay bare.
- **Secondary detail folds (UX-7): `partials/_fold.html`.** A page region carries one idea:
  its headline figures stay open, and what qualifies them (rolling charts, market regimes,
  marker legends, "Left out" and "Not ranked" lists) sits in a `<details class="fold">` whose
  summary is one line naming what is inside. Decisions (agreed):
  - *The heading stays outside the fold, above it.* A summary is a button to a screen reader,
    which flattens a heading inside it, so heading navigation would lose the block. The
    summary then needs only the rest: the count, "2 holdings", or what the block shows,
    "Rolling volatility and Sharpe · 1-year window". *Known cost:* one more line per block
    than a summary that is the heading. A legend has no heading, so its summary names it:
    "What the markers mean · 3 notes".
  - *A count where there is one*, in mono, singular for one (`unit` is a pair).
  - *An empty block isn't folded.* "No rolling point yet" is already one line; a fold would
    only add a click.
  - *Open when the reader just chose it, decided on the server.* A fold renders `open` when the
    request shows the reader asked for what is inside: the Risk correlation pair, the Labs risk
    window (UX-8's `_risk_open`). No JS. Any other `#anchor` into a block lands on its heading
    and closed summary, since every block's id stays on its wrapper outside the fold.
    *Rejected: opening a fold from the URL fragment in a script*, one more file to keep in step
    with the ids for a link only old bookmarks use.
  - *A chart in a closed fold is drawn when the fold first opens* (`risk_rolling_chart.js`). A
    closed `<details>` gives its canvas no box to size from, and the first animation would play
    unseen. `review_pages.py` skips the size check for a canvas still in a closed fold, and its
    `open_folds` states open every fold the way a reader would.
  - *One look.* `.fold` styles every fold's summary: the Risk blocks', the regime groups nested
    in theirs, and the Labs risk section's. Concentration's "Show N more" folds keep their
    muted look: they lengthen a table, and are not detail.
- **Labs (LB-6 to LB-10, RK-9)** has its own file, [`labs-ui.md`](labs-ui.md): the period
  table, the mix form, the growth chart, and the risk panel and drawdown chart.
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
  zeros past the second decimal (`112.34 USD`, `0.1235 USD`). RK-9 added the risk figures'
  `pct1`, `signed_pct1` and `ratio` (spec §10; see [`labs-ui.md`](labs-ui.md)). `/risk` (RK-12)
  uses the same three, so a measure reads the same on both pages. RK-20 added the what-if's
  changes: `signed_points1` (`−0.2 pts`: a change of a percentage, which `−0.2%` would make
  read as a relative one) and `signed_ratio` (`+0.09`). Both are signed, as a delta is, but
  never coloured, since neither is a gain or a loss.
- **Risk (RK-12, RK-14, RK-18, RK-20, RK-23, RK-25, RK-28, RK-29)** has its own file,
  [`risk-ui.md`](risk-ui.md): the section tables, the heatmap, the concentration tables, the
  rolling charts, the regime tables, the what-if and the candidate ranking.
