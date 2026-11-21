# Tasks: UI improvement thread

Roadmap for the UI-improvement chunk. It makes each page show less at once and makes every
instrument recognisable by its full name, not only its ticker. Each page in `web` was built up
chunk by chunk, and no page was ever reworked as a whole. The result:

- **`/risk` is about 12,500px tall on a desktop.** One page holds five major sections: ex-post,
  ex-ante, what-if candidate, ranking and look-through concentration. Each section has its own
  tables, legend notes, rolling charts, regime tables and "Left out" lists.
- **`/labs` puts "Edit the mix" at the very bottom.** The form drives everything on the page,
  but it comes after three tables and two charts.
- **The site header crowds one row** with seven nav links, the user name, Log out and a
  three-button theme switch.
- **Switches look like links.** Period and series switches (`1M 6M 1Y All`,
  `Total return | Market value`) are plain underlined links on Portfolio, Overview, Labs and
  Risk.
- **Tickers appear bare everywhere** (`VWCE.DE`, `AAPL`). A user may not know what a symbol
  stands for.
- **Changing a period reloads the page and jumps to the top.** On Labs and Risk, the period,
  window and sort switches are plain links. A click reloads the whole page and loses the
  reader's place, often far below the top.

Earlier chunks are archived in [`docs/roadmap-archive/`](docs/roadmap-archive/):
[walking skeleton](docs/roadmap-archive/walking-skeleton.md),
[UI/UX](docs/roadmap-archive/ui-ux.md),
[ticker onboarding](docs/roadmap-archive/ticker-onboarding.md),
[portfolio view](docs/roadmap-archive/portfolio-view.md),
[screen layout](docs/roadmap-archive/screen-layout.md),
[labs](docs/roadmap-archive/labs.md) and
[risk](docs/roadmap-archive/risk.md).

**Task IDs are prefixed `UX-`.** The prefix keeps them apart from the walking skeleton's bare
"task N" references and from the earlier chunks' `UI-`, `TK-`, `PF-`, `LY-`, `LB-` and `RK-` IDs.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency.

## Agreed requirements

- **Every ticker can show its instrument's full name.** This applies wherever a ticker appears:
  tables, prose, form fields, the benchmark picker, chart legends and chart tooltips. The name
  shows on hover, on keyboard focus and on tap, so a native `title` attribute alone isn't
  enough. Screen readers announce the name too.
- **The names come from data that already exists.** data-store keeps a `display_name` for every
  tracked ticker (the YAML name, or the provider's long name). web already reads it as
  `TrackedTicker.display_name` through `list_tracked_tickers(tickers=[...])` in
  `services/web/src/web/portfolio_client/base.py`. No schema change and no new endpoint.
- **`/risk` becomes sub-pages under one sub-nav**, one per major section. Collapsible sections
  on one URL and JS tabs were considered and rejected. A collapsible page still loads every
  section's data. JS tabs need a separate no-JS fallback. Sub-pages can be linked and
  bookmarked.
- **Each screen region carries one idea.** The headline figures stay visible. Secondary detail
  (rolling charts, regimes, "Left out" lists, method notes) folds behind a disclosure with a
  one-line summary.
- **A page's main action comes before the results it produces.**
- **Changing a view keeps the reader's place.** A period, window or sort switch updates only the
  section it belongs to. The page doesn't jump, focus stays on the switch, and the URL still
  records the choice, so Back, reload and shared links behave as today. Without JS the switch
  is still a link, and it lands on its section rather than the top of the page.
- **Out of scope:** new `portfolio` endpoints, schema changes, and changes to which figures
  exist. The screen-layout chunk's decision to keep all ten columns on `/portfolio` stands.

## Constraints that apply to every task

- **Brand.** Colours, type and shape come only from `brand/tokens.css` custom properties;
  `test_templates.py` enforces this. Gold is the only accent. Forest and oxblood mean gains and
  losses, nothing else. Every number and every ticker is in `--font-mono`. Copy follows
  chapter IV of `brand/brand-charter.html`.
- **CSP stays `script-src 'self'`.** No inline executable script, no CDN. Server data reaches JS
  through a `<script type="application/json">` data island.
- **Progressive enhancement.** Every page and form works without JS. A `<details>` disclosure
  needs no JS, so it counts as a no-JS solution.
- **DOM order is reading order.** Layout uses CSS, never reordered markup.
- **Phone layout doesn't regress.** Tables still stack at 600px and below. No width in the
  review set scrolls the page sideways.
- **Tests first**, per `.claude/rules/testing.md`. CSS can't be asserted in a unit test, so
  tests cover what is in the markup. Each task is also checked visually: run
  `services/web/scripts/review_pages.py` at every width in both themes and look at the
  screenshots. Screenshots stay local (gitignored).
- **Docs in the same change.** Update `services/web/docs/architecture.md` when a route, module
  or static asset with behaviour is added. Presentation decisions go in `ui-architecture.md`,
  and page-specific ones in `risk-ui.md` or `labs-ui.md`. Routes are listed in `endpoints.md`.
- **File size.** `web.css` and every doc stay under 64KB.

## Status table

| ID | Task | Depends on | Status |
|----|------|-------------|--------|
| UX-0 | [Roadmap housekeeping and baseline](#ux-0-roadmap-housekeeping-and-baseline) | — | done |
| UX-1 | [Ticker name lookup and component](#ux-1-ticker-name-lookup-and-component) | UX-0 | done |
| UX-2 | [Ticker names in tables, text and forms](#ux-2-ticker-names-in-tables-text-and-forms) | UX-1 | done |
| UX-3 | [Ticker names in charts](#ux-3-ticker-names-in-charts) | UX-1 | done |
| UX-4 | [Quieter site header](#ux-4-quieter-site-header) | UX-0 | done |
| UX-5 | [One segmented control for switches](#ux-5-one-segmented-control-for-switches) | UX-0 | done |
| UX-6 | [Risk sub-pages and sub-nav](#ux-6-risk-sub-pages-and-sub-nav) | UX-0 | done |
| UX-7 | [Secondary detail behind disclosures](#ux-7-secondary-detail-behind-disclosures) | UX-6 | done |
| UX-8 | [Labs: mix editor first](#ux-8-labs-mix-editor-first) | UX-0 | done |
| UX-9 | [Copy density pass](#ux-9-copy-density-pass) | UX-6, UX-7, UX-8 | ready |
| UX-10 | [Period switches without a page reload](#ux-10-period-switches-without-a-page-reload) | UX-0 | done |
| UX-11 | [Forms beside the list they add to](#ux-11-forms-beside-the-list-they-add-to) | UX-0 | done |

## Tasks

### UX-0. Roadmap housekeeping and baseline

**Status:** done

- Ask the user whether this thread takes over the archived Deferred item "Forms beside the list
  they add to". If it does, add it to [Deferred](#deferred).
- Regenerate the screenshots with `review_pages.py` before any change. They are the baseline
  that later tasks are compared against. This matters most for Risk, Labs, Portfolio, Overview
  and the header.

Done as:
- Forms beside the list: taken on as a task, [UX-11](#ux-11-forms-beside-the-list-they-add-to),
  rather than a Deferred item.
- Baseline: written to `services/web/screenshots/baseline/` (gitignored), apart from the usual
  `screenshots/` folder, so later runs don't overwrite it. 820 screenshots (every page state,
  both themes, five widths); all 820 passed the script's checks, so the baseline starts clean.

### UX-1. Ticker name lookup and component

**Status:** done

This task builds the foundation that UX-2 and UX-3 apply everywhere.

- **Lookup.** A web helper takes the set of tickers a page shows and returns
  `{ticker: display_name}` from one `list_tracked_tickers(tickers=[...])` call. It leaves out a
  name equal to its ticker, since that adds nothing.
- **Failure isolation.** If the lookup fails, the page renders with bare tickers, not an error
  page. A missing name is cosmetic.
- **Component.** A Jinja macro in `partials/_ticker.html`, built like `partials/_term.html`. It
  renders the ticker in mono inside a focusable element. A CSS tooltip with the full name shows
  on `:hover` and `:focus-visible`. A tap focuses the element, so touch gets the same tooltip.
  The name is also in the accessible text, so a screen reader reads "VWCE.DE, Vanguard FTSE
  All-World".
- **No name, no tooltip.** A ticker with no known name, such as an untracked symbol or an
  ISIN-only row, renders as plain text with no empty tooltip.
- **Tooltip placement.** The tooltip stays inside the viewport at phone width and doesn't push
  table columns wider.

Tests: macro output with and without a name. The helper returns names, drops names equal to
their ticker, and returns an empty map when the client raises.

Files: new helper module under `services/web/src/web/`, `templates/partials/_ticker.html`,
`static/web.css`, `docs/architecture.md`, `docs/ui-architecture.md`.

Done as:
- **Helper.** `ticker_names.load_names(client, tickers)`, modelled on
  `ticker_status.load_statuses`. It also drops empty names. It catches only
  `PortfolioClientError`, so a bug in web still surfaces.
- **Macro.** `ticker(symbol, names, focusable=true)`. Use `focusable=false` for a ticker inside
  a link, button or label, where a nested tab stop is invalid; the host opens the tooltip.
  UX-2/UX-3 call sites that need it: `/portfolio`'s `ticker-toggle` button, the Risk
  "Exclude/Include TICKER" links and the Labs "Assets on the chart" labels.
- **Tooltip escapes scroll boxes.** Most ticker tables sit in a `.table-scroll` box, which
  clips an absolute tooltip. So the open tooltip is `position: fixed` and uses CSS anchor
  positioning, flipping to stay on screen. Browsers without anchor positioning fall back to
  the absolute tooltip.
- **Opens on `:focus`, not `:focus-visible`.** A tap doesn't match `:focus-visible`.
- **Escape closes it.** `static/ticker_tooltip.js` handles Escape (WCAG 1.4.13).
- **Checked visually.** The macro was wired into the Labs returns table and checkbox labels
  temporarily, then reverted. At 360px and 1280px in both themes:
  - hover, tap, focus and the label host open it
  - it stays inside the viewport and isn't clipped by `.table-scroll`
  - it doesn't widen its cell
  - Escape closes it
- **For UX-2.** The holdings pages already call `load_statuses`. Adding `load_names` makes a
  second `list_tracked_tickers` call unless the two share one fetch.

### UX-2. Ticker names in tables, text and forms

**Status:** done

Apply the UX-1 component at every place a template prints a ticker. A grep for `.ticker }}` in
`templates/` finds them. They include:

- holdings on `/portfolio` and account holdings
- transactions and the Tickers list
- Labs tables, "Not backtested" and "Left out"
- Risk tables, regime tables, what-if, ranking and look-through funds

Pickers can't hold the tooltip, so they show the name in the option itself:

- **The benchmark picker** shows options as `VWCE.DE — Vanguard FTSE All-World`.
- **The Labs mix inputs** suggest tickers the same way. The value submitted is still the bare
  ticker.

Each route gathers its page's tickers and passes `ticker_names`.

Tests: for every page that shows a ticker, a fake client with names makes the page render them.
A page whose name lookup fails still renders.

Done as:
- **One unfiltered fetch per page**, not "each route gathers its page's tickers". The pickers
  offer every priced ticker (~1.3k), too many for one filtered query string. Only tracked
  tickers have names, and there are few of them. `ticker_names.load_tracked` makes the call,
  and `load_names` builds the map from it.
- **Holdings pages make one call, not two.** The same rows give names and statuses
  (`ticker_status.statuses_from` replaced `load_statuses`). A page with no holdings makes no
  call.
- **Pickers.** The benchmark fields, the Labs mix inputs and the Risk what-if candidate use
  `ticker_option`. Its `label` is `VWCE.DE — Name`, so Firefox, which shows only the label,
  still shows the ticker. Chromium shows the ticker twice. Not checked by eye: a native
  datalist popup doesn't render in a headless screenshot. The new-transaction and
  admin-mapping pickers are left bare.
- **Not changed.** The Tickers list already has a Name column. Risk's ranking rows and their
  "Left out" list already print the name. The ranking's "Not scored" list is named.
- **Fund constituents stay bare.** Only tracked tickers have a `display_name`.
- **Tab stops.** /risk went past `review_pages.py`'s 120-stop walk, so its cap is now 200. A
  regime note names the benchmark only at its first mention.
- **Checked visually** at 360px: the /portfolio toggle button hosts the tooltip, a focused Risk
  ticker opens it, and Escape closes it. Every reviewed page passed `review_pages.py`.

### UX-3. Ticker names in charts

**Status:** done

- Each chart's data island carries `ticker_names`.
- Chart legends and hover tooltips label a series "TICKER — Name". This covers
  `labs_chart.js`, `labs_drawdown_chart.js`, `risk_rolling_chart.js` and
  `holdings_breakdown.js`.
- The Labs "Assets on the chart" checkboxes use the `_ticker` component.

Tests: the data islands contain the names. Check chart labels visually with `review_pages.py`.

Done as:
- **Only the Labs growth chart draws a ticker.** Of the four scripts listed:
  - `labs_chart.js`: changed.
  - `labs_drawdown_chart.js`: one "Drawdown" line for the mix. Not changed.
  - `risk_rolling_chart.js`: its lines are "Volatility", "Sharpe" and "Correlation". Not
    changed; the correlation's pair is named in the markup beside the chart instead.
  - `holdings_breakdown.js`: not a chart. It is /portfolio's row toggle, whose button UX-2
    named.
- **A name per line, not a `ticker_names` map.** The growth island's assets and benchmark each
  carry `name` (`null` when there is none), so the island holds only the names of lines it
  draws.
- **Tooltip.** `VWCE.DE — Vanguard FTSE All-World: +7.07%`. Chart.js doesn't wrap a tooltip
  line, so the name is cut with `…` to fit the canvas (`chart_theme.js` `fitText`), or left out
  when almost nothing fits.
- **End-of-line labels stay bare.** Their room comes out of the plot's width.
- **"Assets on the chart" labels** use `ticker(..., focusable=false)`.
- **Label hosts open on `:focus-within`.** UX-1's CSS opened a host's tooltip on `:focus`, which
  a label never has: focus is on its box. Fixed in `web.css` and `ticker_tooltip.js`.
- **Risk's correlation pair, not in the task text.** UX-2 left its note and its two selects
  bare. The note now uses the macro, and each option reads `AAPL — Apple Inc.`, with
  `(benchmark)` after the name. The selects are capped at their row's width: with names in
  them they pushed the page sideways at 360px.
- **Known cost.** A disabled box can't take focus, so an asset with no line shows its name on
  hover only.
- **Checked visually** at 360px and 1280px in both themes, on review data with names:
  - the tooltip stays inside the canvas and cuts the benchmark's name at 360px
  - toggling a line still redraws the chart
  - a focused box opens its label's name, inside the viewport, and Escape closes it
  - the pair's selects stay inside their card
- **`review_pages.py`:** all 820 screenshots passed its checks.

### UX-4. Quieter site header

**Status:** done

- **Main nav** keeps only the sections: Overview, Portfolio, Labs, Risk, Accounts, Tickers.
- **Account menu.** The user's name becomes a `<details>` menu, which works without JS. It
  holds Admin (admins only), the Light/Dark/System theme switch and Log out. The theme form's
  plain POST and its htmx swap keep working.
- **Phone width.** Nav and menu fit without sideways scroll.

Tests: `aria-current` still marks the current section. The menu lists Admin only for admins. The
theme form posts as before.

Files: `templates/base.html`, `static/web.css`, `static/theme.js` if needed,
`docs/ui-architecture.md`.

Done as:
- **Menu.** A `<details class="account-menu">` labelled with the user's name. It holds Admin
  (admins only), the theme switch and Log out, in that order, with a hairline above Log out.
  On an admin page, the menu's Admin link is the one marked `aria-current`.
- **Logged out.** Login, signup and an anonymous 404 keep the bare theme switch in the header,
  since there is no user to name a menu after. One `theme_toggle()` macro in `base.html`
  renders both. `theme.js` didn't change.
- **Closing.** `static/account_menu.js` closes the menu on Escape (focus returns to the name), on
  an outside click, and when focus leaves it. A theme click inside leaves it open.
- **Phone width.** The six sections fit on one line at 360px (tighter gap, `space-between`); the
  name shares the first row with the mark.
- **Checked.** `review_pages.py` gained `account-menu-admin` and `account-menu-user` states.
  The header pages passed at every width in both themes. Escape, outside click, tab-out and the
  theme switch were checked in a browser. The UX-0 baseline folder wasn't in this checkout, so
  the comparison was by eye, not side by side.
- **Theme buttons still look like links.** UX-5 left the header's switch out because UX-4 moves
  it, and UX-4 kept its look, so neither task gave it the segmented control.

### UX-5. One segmented control for switches

**Status:** done

- **One component.** A partial and its CSS replace the underlined-link switches: series and
  period on Overview, Portfolio and account holdings; growth period and window on Labs; and the
  same on Risk.
- **It reads as a control.** It shows a clear selected state that doesn't rely on colour alone.
  The selected option carries `aria-current` (for links) or `aria-pressed` (for buttons).
- **No-JS behaviour.** Options stay links or form buttons, so they work without JS as they do
  today.

Tests: each page renders the component, with exactly one option selected that matches the query
parameter.

Done as:
- **`partials/_segmented.html`, two macros.** `segmented_links(label, links, current, labels,
  mono, class_)` takes the routes' existing `{value: url}` link dicts, so no route changed.
  `segmented_buttons(label, options, pressed, data_attr, mono)` draws the returns chart's view
  and range; `portfolio_chart.js` is unchanged.
- **A labelled `role="group"`, not `<nav>`.** A view switch isn't site navigation, and /risk
  would otherwise gain a landmark per switch.
- **Selected state.** Filled, bold, with a gold bar along its foot (the site nav's
  `aria-current` mark). Period and window labels are in mono.
- **Ranking order.** The two links inside the note's sentence became an "Order by" control above
  it, labelled "Volatility" and "Diversification ratio"; the note now names the chosen measure.
- **Not changed.** The header's theme switch (UX-4 moves it) and the Exclude/Include links, which
  are per-row actions, not switches.
- **Checked visually.** Overview, Portfolio, Labs, Risk and the ranking, in both themes. Options
  are padded 8px a side so Labs' seven growth periods fit one row at 360px; the control still
  wraps if a narrower box ever needs it. `review_pages.py`: 819 of 820 passed. The one miss, a
  0px-wide rolling chart on `risk-concentration-all` (dark, phone), passed on a rerun of that
  page: a flaky layout timing, not this change.

### UX-6. Risk sub-pages and sub-nav

**Status:** done

Split `/risk` into four routes, each loading only its own data:

| Route | Contents |
|-------|----------|
| `/risk` | Ex-post: invested assets |
| `/risk/ex-ante` | Ex-ante: constant weights |
| `/risk/what-if` | What-if: adding a candidate, and ranking the tracked tickers |
| `/risk/concentration` | Concentration: look-through |

- **Sub-nav.** A partial modelled on `partials/_account_header.html` heads every sub-page and
  marks the current one with `aria-current`.
- **Split along existing seams.** `api/risk.py` already loads each section on its own, with its
  own error notice, so the split follows those seams. The risk template splits per sub-page;
  shared macros move to partials.
- **Benchmark choice.** The chosen benchmark carries across sub-pages in the sub-nav links.
- **Old links.** Links to the old in-page section anchors land on the matching sub-page.

Tests: each route renders only its section and calls only its own client methods. Sub-nav
marking is correct. A section failure shows its notice on its own sub-page. The benchmark
parameter survives navigation.

Files: `api/risk.py`, `api/routes.py`, `templates/risk*.html`, new sub-nav partial,
`docs/endpoints.md`, `docs/architecture.md`, `docs/risk-ui.md`.

Done as:
- **Four routes, four templates.** `risk.html`, `risk_ex_ante.html`, `risk_what_if.html` and
  `risk_concentration.html`, each headed by `partials/_risk_header.html`. Macros more than one
  page needs (`figure`, `summary_row`, and new `legend` and `left_out`, for the blocks UX-7
  wraps) are in `partials/_risk_figures.html`. `api/routes.py` didn't change.
- **The sub-nav carries the whole query, not only the benchmark** (agreed). The what-if and
  ranking use ex-ante's window and exclusions, so carrying only the benchmark would have
  changed their "before" on the way over. A URL can carry params its page ignores.
- **The what-if page names ex-ante's window and exclusions** and links to Ex-ante to change
  them (agreed), rather than repeating the window switch.
- **The benchmark field is also on `/risk/ex-ante`.** Not in the task text: that section sends
  the benchmark too.
- **Old links need JS.** A fragment never reaches the server, so `static/risk_anchor.js` on
  `/risk` sends `/risk#risk-ranking` and the other old section anchors on to their sub-page.
  Without JS they land on ex-post.
- **Copy.** Only what the split made false ("the ex-post ones above", "the ex-ante window
  above"); the rest is UX-9's.
- **For UX-10.** Section and region ids are unchanged. The ex-ante window switch exists only
  on `/risk/ex-ante`, and the ranking's sort only on `/risk/what-if`.

### UX-7. Secondary detail behind disclosures

**Status:** done

Each Risk sub-page keeps its headline table open. Secondary detail moves into `<details>`
blocks, each with a one-line summary that names what is inside, such as "Rolling volatility ·
1Y window". That covers:

- rolling charts
- market regimes
- legend footnotes
- the "Left out" and "Not ranked" lists

The rules:

- The disclosure's summary gives the count where there is one, such as "Left out ·
  2 holdings".
- A chart that is drawn while closed must size correctly when opened. Check
  `risk_rolling_chart.js`.
- Labs gets the same treatment for "Not backtested" and "Left out". Its risk section is
  already folded (UX-8); UX-7 brings that fold's look in line with the others.

Tests: each moved block renders inside a `<details>` with its summary and count. Nothing that
renders today is dropped.

Done as:
- **One macro, `partials/_fold.html`**, and one `.fold` style for every fold, the Labs risk
  fold and the regime groups included. Concentration's "Show N more" folds keep their look:
  they lengthen a table, and aren't detail.
- **Heading outside, fold below (agreed).** Each block keeps its heading. A summary is a
  button to a screen reader, which flattens a heading inside it. So summaries are short:
  "2 holdings", "Rolling volatility and Sharpe · 1-year window". The legend has no heading,
  so it reads "What the markers mean · 3 notes".
- **Folded:** legends, rolling charts, the correlation pair, market regimes, Left out, Not
  ranked, and the ranking's over-cap list. The over-cap list was a sentence, so it gained a
  "Not scored" heading. On Labs, "Not backtested" and "Left out"; Left out gained a heading.
- **Stays open:** the ex-ante VaR lines, the heatmap and its shade key, the effective-number
  and threshold notes, and the Labs notes on what a period's figures leave out. Each is a
  figure, or what you need to read the one beside it.
- **Regimes: one outer fold (agreed)**, "9 regimes in 4 groups", with the groups nested and
  closed inside it.
- **An empty block isn't folded.** "No rolling point yet" is already one line.
- **Deep links (agreed).** The server opens the pair's fold when the URL names a pair, so the
  pair form lands on its chart. Any other anchor lands on its heading and closed summary,
  since block ids stay outside the folds. No JS.
- **Charts.** `risk_rolling_chart.js` draws a chart in a closed fold when the fold first
  opens. A closed fold gives the canvas no box to size from.
- **`review_pages.py`.**
  - It gained an `open_folds` flag, which clicks every visible closed fold.
  - New states: `*-folds-open` per Risk sub-page, and `labs-folds-open`.
  - The rolling states open their folds; the regime states open the outer fold first.
  - The chart-size check skips a canvas still in a closed fold.
- **Page height.** Ex-post `/risk` at 1280px is 2,194px closed and 4,944px with every fold
  open.
- **Checked.** `review_pages.py` passed 949 of 950. The miss was `labs-long-mix-added`, whose
  drawdown chart was 0px wide. That was an existing flake: it failed on 3 of 4 runs on `main`
  without this change.
  - Screenshots checked by eye: `/risk` closed at 1280px, opened charts and nested regimes at
    360px in dark, and the open Labs folds at 1280px.
- **With UX-10**, which landed first. The Labs comparison region sits inside the risk fold,
  so a window swap replaces only the region and the fold stays open. The ranking and ex-ante
  regions hold folds of their own, such as Not ranked and the legends. A swap re-renders those
  closed, as a reload would. Merging UX-10 changed two things:
  - The swap check skips charts in a closed fold.
  - `open_folds` now runs after the swap. A new state, `risk-ex-ante-window-swap-folds-open`,
    checks that swapped-in charts draw when their fold opens.

### UX-8. Labs: mix editor first

**Status:** done

- **Editor first.** "Edit the mix" moves to the top of `/labs`, right after the intro. On wide
  screens it is a compact card beside the returns table; on narrower ones it sits above it.
- **Results order.** Results follow as: returns table, then growth chart, then drawdown. The
  risk and window tables go behind a disclosure, per UX-7.
- **Editor behaviour.** Update, Add another asset and Reset behave as today. The running total
  stays next to the inputs.

Tests: in DOM order the form comes before the results. Form behaviour tests stay green.

Files: `templates/labs.html`, `static/web.css`, `docs/labs-ui.md`.

Done as:
- **Layout.**
  - Cards in DOM order: an intro card; then a `.split-labs` row with the editor card and a
    "Returns" card; then one card with growth, drawdown, risk, "Not backtested" and Left out.
  - The editor and table sit side by side from 1600px (breakpoint `labs-split`): the editor
    takes at least 400px, the table about 1000px plus padding. So review width `wide` shows
    two columns, and `laptop` and below stack, editor first.
- **Long mix (agreed): the rows scroll in their own box.** LB-7 had rejected "form above the
  table" because 50 rows would push the figures down.
  - The box holds about eight rows, then scrolls, with a hairline where it cuts off.
  - A box that starts with more than eight rows is a labelled, focusable region, so a keyboard
    can scroll it. `labs_form.js` does the same once added rows overflow, and scrolls the new
    row into view.
  - Known cost: the column labels and a row's error can scroll out of view.
- **Risk folded now (agreed), not left for UX-7.**
  - The "Risk" heading and `#labs-risk` stay outside a `<details>` that holds the rest.
  - It starts open for a window other than 1Y, a benchmark other than the default, or a
    benchmark error (`api/labs._risk_open`). The mere presence of the parameters doesn't open
    it: the mix form and the period links always send them.
- **Copy without directions.** The editor is beside the table on one screen and above it on
  another, so "below" became "in the mix" or "of your own" in the empty states and the
  subtitle.
- **Checked.**
  - `review_pages.py` gained `labs-long-mix`, `labs-long-mix-added` and `labs-risk-open`.
  - Every Labs page passed at every width in both themes (190 of 190).
  - Screenshots checked by eye at 1920px, 1280px and 360px.

### UX-9. Copy density pass

**Status:** ready

Done last, once the structure has settled.

- **Intros.** Every page and section intro becomes one plain sentence plus its glossary link.
  Longer method notes move to the glossary.
- **Repeated notes.** Notes like "Hover a figure for its window…" and as-of lines that repeat
  within a page appear once per page.
- **Voice.** Copy follows chapter IV of the brand charter.

Tests: page tests that assert copy are updated. Glossary links still land (existing tests).

### UX-10. Period switches without a page reload

**Status:** done

**Scope.** These switches reload the whole page today:

| Page | Switch |
|------|--------|
| Labs | Growth period (it drives both the growth and drawdown charts) |
| Labs | Risk comparison window |
| Risk | Ex-ante window |
| Risk | Exclude and Include ticker links |
| Risk | Ranking sort (volatility or diversification ratio) |

The returns chart on Portfolio, Overview and account holdings already switches in the browser
(`static/portfolio_chart.js`). It is out of scope.

The page jumps for two reasons. Each switch is a plain link built server-side (for example
`_period_links` in `api/labs.py`). Each chart script draws once, on `DOMContentLoaded`
(`labs_chart.js`, `labs_drawdown_chart.js`, `risk_rolling_chart.js`).

The approach:

1. **Swap one section, not the whole page.** Every switch above sits inside a region with a
   stable `id`: the Labs growth-and-drawdown block, the Labs comparison block, the ex-ante
   block and the ranking block. Each link gets these htmx attributes:
   - `hx-get` (same URL as `href`)
   - `hx-target` and `hx-select` (its region's `id`)
   - `hx-swap="outerHTML show:none"`
   - `hx-push-url="true"`

   htmx is already vendored and used for forms. With `hx-push-url` the address bar keeps the
   chosen period, so reload, Back and a shared link still show it.
2. **Server sends only the region on an htmx request.** If the request has `HX-Request` and
   `HX-Target`, the route renders only that region's partial. This uses the existing
   `api/htmx.py` `is_htmx`. Otherwise the route renders the full page as today. The server
   does the same work as today; it only sends less back. Moving the regions into partials is a
   preparatory refactor, done first.
3. **Charts redraw after a swap.** Each chart script gets an init function. It runs on
   `DOMContentLoaded` and again on `htmx:afterSettle` for charts inside the swapped region.
   Before drawing it destroys any `Chart` already on that canvas (`Chart.getChart`).
   `chart_theme.js` still applies.
4. **Feedback and focus.**
   - **Loading.** While the request runs, the region gets `aria-busy="true"` and is visibly
     dimmed, using htmx's `htmx-request` class and a tokens-only style. The switch is disabled
     to stop double clicks.
   - **Focus.** After the swap, focus returns to the newly selected switch.
   - **Screen readers.** A polite live region says what changed, such as "Growth over 5Y".
5. **Errors stay in the section.** If the request fails, the section shows the existing error
   banner (`htmx-error-swap.js`) and the rest of the page stays as it was.
6. **No-JS fallback.** Every `href` gains its region's `#anchor`, as `benchmark_form` already
   does with `anchor=`. Without JS, a click reloads the page but lands on its section, not the
   top.

**Coordination.**
- UX-5's segmented control must carry these attributes. UX-5 landed first: add them, and the
  section anchor, in `segmented_links` (`partials/_segmented.html`), which every switch in scope
  already uses.
- UX-6 moves the ex-ante and ranking blocks to their own sub-pages. The region ids move with
  them unchanged.
- UX-8 reorders Labs. The regions stay self-contained, so the order doesn't matter to them.

**Tests:**
- An htmx request (`HX-Request` and `HX-Target` headers) returns only the region, with the right
  period selected.
- A plain request returns the full page.
- Every switch's `href` ends with its region's anchor and its `hx-*` attributes match it.
- An error in an htmx request returns the section's error markup, not an error page.
- In the browser, with `review_pages.py` or by hand:
  - after a switch the scroll position is unchanged
  - the charts are redrawn
  - Back restores the previous period

**Files:**
- `api/labs.py` and `api/risk.py`
- new region partials under `templates/partials/`
- `templates/labs.html` and `templates/risk.html`
- `static/labs_chart.js`, `static/labs_drawdown_chart.js` and `static/risk_rolling_chart.js`
- `static/web.css`
- `docs/ui-architecture.md` (partial-swap pattern), `docs/labs-ui.md` and `docs/risk-ui.md`
- `docs/architecture.md`, because routes now return partials

Done as:
- **One task, Labs and Risk (agreed).** A split was planned while UX-6 was open, since the
  ex-ante window also changed the what-if and the ranking on the one `/risk` page. UX-6 landed
  first, so Risk came in here. The ex-ante window and Exclude/Include swap `#risk-ex-ante` on
  `/risk/ex-ante`; the order swaps `#risk-ranking` on `/risk/what-if`.
- **Out-of-band carriers (agreed), not in the task text.** Other elements carry the same query,
  and would have undone a switch:
  - the other switch
  - the benchmark and mix forms' hidden inputs (`partials/_kept.html`)
  - Risk's sub-nav (it carries the whole query since UX-6)
  - `/risk/what-if`'s window line

  The swap response (`*_swap.html`) sends each again with `hx-swap-oob`.
- **`hx-get` drops the anchor**, unlike the task text's "same URL as `href`": htmx 1.9 scrolls
  to a request URL's anchor after the swap. `hx-push-url` keeps it.
- **Three htmx settings in `base.html`.**
  - `historyCacheSize: 0`, so no page snapshot lands in `localStorage`, where its figures would
    outlive logout.
  - `refreshOnHistoryMiss`, so Back reloads the previous URL.
  - `attributesToSettle: ["class"]`. htmx's default copies `style` onto a swapped-in canvas with
    `setAttribute`, which the CSP blocked.
- **`overflow-anchor: none` on regions.** htmx inserts the new region before removing the old,
  and Chrome's scroll anchoring followed an old node: the phone ranking jumped 2,800px.
- **Double clicks.** `hx-sync` replaces the request rather than disabling the switch; the region
  is dimmed below its heading and switch.
- **Focus** returns to the selected option, or to the link for the same ticker (Exclude X
  becomes "Include X again"), else to the heading. The announcement is server-written
  (`data-swap-announce`).
- **Charts.** `chart_theme.js` `mount` draws on load and after each swap, so scripts load once,
  outside the regions. The rolling-chart scripts now load on every `/risk/ex-ante` view.
- **The ranking's order still reruns the what-if**, as the full page did. The server work is
  unchanged everywhere; only the response shrinks.
- **Checked.**
  - `review_pages.py` gained five `*-swap` states. Each asserts the scroll, the URL, the focus,
    the redrawn charts, and that Back undoes it. All 50 passed.
  - By hand in a browser: the dimmed busy state, a 500 and a dropped connection (banner in the
    section, old content kept), the Exclude-to-Include focus, the announcement, and a no-JS click
    landing on its section.


### UX-11. Forms beside the list they add to

**Status:** done

This item was carried over from the screen-layout chunk's Deferred list, where it was left out
by choice. It is taken on here because of UX-0. Today every form keeps its narrow card. On a
wide screen, a form ends up below or away from the list it changes, so the reader can't see
both at once.

| Page | Today | On wide screens |
|------|-------|-----------------|
| Admin mappings | "Add or change a mapping" sits below the two full-width tables | The form sits beside "Waiting for a mapping" and the mappings table |
| Accounts | The list and the form share one narrow card | The list and the form are two cards side by side |
| Add transaction | A page of its own; the list is on the Transactions tab | The account's recent transactions sit beside the form |
| Add a ticker | A page of its own; the list is on `/tickers` | The user's tracked tickers, with status, sit beside the lookup |

- **Reuse the `.split` grid** in `static/web.css`, which the holdings pages already use. The
  form card keeps its form-tier width, and the list takes the rest. Below the split breakpoint,
  the cards stack as they do today.
- **The extra lists come from existing client methods**: the transactions list and
  `list_tracked_tickers`. No new `portfolio` endpoint. Each list loads on its own, so if it
  fails, the form still works and the list shows a notice in its place.
- **Reading order is an assumption to confirm when the task starts.** On Accounts and Admin
  mappings, the list comes first in the DOM: it is what the page is for, and the form changes
  it. On Add transaction and Add a ticker, the form comes first: the page is for the form, and
  the list is context. Stacked on a phone, the cards follow the same order.
- **After a successful add, the new row appears in the list beside the form.** This is a
  redirect back to the same page, as today, unless the htmx form swap already covers it.

Tests:
- Each page renders the form and the list inside the split container, in the agreed order.
- A failure loading the side list keeps the form and shows the notice.
- Form behaviour tests stay green.

Visual check with `review_pages.py`:
- no sideways scroll
- no `.table-scroll` box that scrolls while two columns show

Files:
- `templates/accounts.html`, `templates/admin_mappings.html`, `templates/transactions_new.html`
  and `templates/tickers_new.html`
- their routes in `api/`
- `static/web.css`
- `docs/ui-architecture.md`, whose "forms keep their narrow cards" statement is rewritten
- `scripts/review_pages.py`, to add page states if needed

Done as:
- **Reading order as assumed** (confirmed). `.split-form` puts the list first (Accounts, Admin
  mappings), `.split-form-start` the form (Add transaction, Add a ticker). A column holding two
  cards (mappings' tables; the lookup and its result) is a `.split-form-column`. Both are
  `.split` modifiers: none of the existing ones sizes a form column.
- **Breakpoint `form-split`, 1100px.** The list column's minimum is the mappings table's 559px
  plus padding, the form's 360–440px. A list-first form is sticky in a tall window.
- **`web.css` was 62.7KB.** A preparatory step moved the `.split*` rules into a new
  `static/layout.css`, linked after it. The template tests read both files.
- **Accounts (agreed):** the New account form is always open in its own card; the `<details>`
  went. "Create an account below" lost its direction.
- **Admin mappings:** the section headings became `h2`, under the page's one `h1`.
- **Add transaction (agreed):** the 10 newest transactions (date, type, named instrument,
  amount), no Delete, and a link to the Transactions tab.
- **Add a ticker (agreed):** the tickers the viewer added, admins included, with status and a
  link to `/tickers`. `POST /tickers` now redirects back to `/tickers/new?added=` (or
  `already=`), whose banner `/tickers` used to show; admin changes still land on `/tickers`.
- **Each side list loads on its own**, and lazily on a full-page error re-render.
- **Checked.** `review_pages.py` gained `tickers-new-added`. Every affected page, and the pages
  whose CSS moved, passed at every width in both themes (100 of 100). The breakpoint was
  probed at 1099px and 1100px. Not covered by a review state: more than 10 transactions (the
  fake account has 4; a unit test covers the cap) and an Accounts first run.

## Deferred

- **Names for tickers data-store doesn't track**, such as an unmapped ISIN. These need a
  data-store lookup that doesn't exist yet. Until then they render bare (UX-1).
