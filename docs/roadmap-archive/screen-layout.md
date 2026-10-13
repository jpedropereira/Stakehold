# Screen-layout roadmap (archived)

Completed 2026-10, tasks LY-0 to LY-7. This is the screen-layout chunk's `tasks.md`, moved here
when the next roadmap chunk (labs) started in
[`tasks-labs-thread.md`](../../tasks-labs-thread.md). The content is verbatim except for relative
links, adjusted for the new location. Its Deferred list is carried forward into that file.

---

# Tasks

Roadmap for the screen-layout chunk: making the web UI use a desktop screen instead of stacking
every page into one narrow centred column. Today every data page is capped at 880px inside a
1120px page box. A 1280px screen leaves about 400px empty, a 1920px one more than 1000px, and the
holdings pages put the table above the chart, so seeing both means scrolling past one.
The previous chunks are archived in [`docs/roadmap-archive/`](./):
[walking skeleton](walking-skeleton.md),
[UI/UX](ui-ux.md),
[ticker onboarding](ticker-onboarding.md) and
[portfolio view](portfolio-view.md).

**Task IDs are prefixed `LY-`** so they can't be confused with the walking-skeleton's bare
"task N" references or the earlier chunks' `UI-N`, `TK-N` and `PF-N`.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency.

## Agreed requirements

- **Designed for screens up to 1920px.** Content grows with the window up to 1600px, then
  centres. That leaves about 160px each side on a 1920px screen, and table rows on wider
  screens stay short enough to read.
- **Holdings pages put the table and the chart side by side** (`/portfolio` and
  `/accounts/holdings`). On a wide screen, the holdings table and the returns chart sit in two
  columns, the table wider, with the as-of line and summary tiles spanning both above them.
  Below a breakpoint they stack as they do today.
- **`/portfolio` keeps all ten columns.** On laptops (1280–1440px), where the ten columns and the
  chart don't fit side by side, the cards stack at full width. Compacting the table, with € and %
  in one cell or hidden columns, was considered and rejected, because it changes how returns
  read.
- **Overview (`/`) gets the same treatment.** Its tiles span the top. Below them, the accounts
  list sits beside the cross-account returns chart, the same chart `/portfolio` shows (LY-6).
- **Wide tables use the content width.** Transactions, Tickers and the admin mapping tables
  fill the content width instead of the 880px card. Transactions no longer overflows its card.
- **Out of scope for this chunk:** putting forms beside the list they add to (Accounts, Add
  transaction, Add a ticker, Add or change a mapping). Forms keep their narrow cards. See
  Deferred.

## Constraints that apply to every task

- **web only.** Templates, `static/web.css`, `static/portfolio_chart.js`,
  `scripts/review_pages.py` and the web routes. No portfolio or data-store change and no schema
  change. If a task turns out to need one, stop and agree it first.
- **Phone layout doesn't regress.** Tables still stack at 600px and below
  (`docs/ui-architecture.md`, "Stacked tables"). No width in the review set may scroll the page
  sideways.
- **DOM order is reading order.** Columns are placed with CSS grid, never by reordering markup,
  so screen readers and keyboard users meet the page in the same order at every width. On the
  holdings pages that order is tiles, then table, then chart.
- **Breakpoints are named once.** CSS custom properties can't be used in `@media`, so every
  breakpoint value is listed in a comment at the top of `web.css` next to `--content-max`, and
  each media query cites it by name.
- **Brand.** Tokens only (no hex values), every number in `--font-mono`, gold the only accent,
  and forest and oxblood only on gains and losses. More room is not a reason to add decoration.
- **Tests first**, per `.claude/rules/testing.md`. CSS layout can't be asserted in a unit test,
  so tests cover what is in the markup: which cards a page renders, in what order, inside which
  layout container, and error isolation for anything a page newly loads. Each task is verified
  visually with `review_pages.py` at every width in LY-1's set, and its screenshots are
  regenerated and looked at in the same change. They stay local (gitignored), not committed.
- **Docs in the same change.** `services/web/docs/ui-architecture.md` holds the layout reasoning.
  Statements that become false are rewritten, including the PF-4/PF-6 note on `/portfolio`'s
  `.content-wide` and `.table-scroll`. `web.css` (27 KB) stays under 64 KB.

## Status table

| ID | Task | Depends on | Status |
|----|------|-------------|--------|
| LY-0 | [Roadmap housekeeping](#ly-0-roadmap-housekeeping) | — | done |
| LY-1 | [Review at tablet and wide widths](#ly-1-review-at-tablet-and-wide-widths) | — | done |
| LY-2 | [Wider content and wide tables](#ly-2-wider-content-and-wide-tables) | LY-1 | done |
| LY-3 | [Chart sized by its container](#ly-3-chart-sized-by-its-container) | LY-1 | done |
| LY-4 | [`/portfolio`: table and chart side by side](#ly-4-portfolio-table-and-chart-side-by-side) | LY-2, LY-3 | done |
| LY-5 | [Account holdings: table and chart side by side](#ly-5-account-holdings-table-and-chart-side-by-side) | LY-4 | done |
| LY-6 | [Overview: two-column layout](#ly-6-overview-two-column-layout) | LY-4 | done |
| LY-7 | [Retire the 880px column](#ly-7-retire-the-880px-column) | LY-5, LY-6 | done |

## Tasks

### LY-0. Roadmap housekeeping

**Status:** done

The portfolio-view chunk moved to
[`docs/roadmap-archive/portfolio-view.md`](portfolio-view.md). Its content is
verbatim apart from relative links. This file was then started. The Deferred list is carried
over whole, and its Known limitations stay with that chunk, since they describe its features.

### LY-1. Review at tablet and wide widths

**Status:** done

`scripts/review_pages.py` screenshots at 1280px and 360px only, so it can't see the problem
this chunk fixes or the regressions it could cause. The empty sides only show at 1920px.
Between 600px and about 990px, `/portfolio` used to scroll the whole page sideways, and the
script missed it (ui-architecture.md, PF-4 note).

- Widths become phone 360, tablet 768, desktop 1280 and wide 1920. Add 1440 if LY-4's
  breakpoint lands near it.
- The no-sideways-scroll check runs at every width, not just phone.
- New check: no table is wider than the card that holds it, unless it sits in a `.table-scroll`
  region. That catches the Transactions table, which overflows its card at 1280px today.
- Regenerate the screenshots. They're gitignored and stay local; committing about 190 PNGs on
  every refresh was rejected.
- `--width` narrows a run to some widths, like `--only` does for pages, for faster loops in
  LY-2 to LY-7.

This task changes no page. Its output is the baseline that later tasks are compared against.
The baseline is recorded in `docs/ui-architecture.md` (review section): 180 of 192 variants
pass, and the 12 failures are the Transactions table (with and without its delete dialog)
overflowing its card at tablet, desktop and wide, and scrolling the page at tablet. Known
failures stay failures rather than going on an allowlist, so the script exits non-zero until
LY-2 fixes them.

Files: `scripts/review_pages.py`, `screenshots/`, `docs/ui-architecture.md` (the review section).

### LY-2. Wider content and wide tables

**Status:** done

The layout foundation, applied first to the pages that are just one wide table.

- **Raise `--content-max`** from 1120px to 1600px. The site header shares the variable, so the logo and nav line up
  with the content edge again. Today the logo sits about 110px left of the cards.
- **Width tiers instead of one-off `max-width: 880px` rules.** There are three: *narrow* (auth
  cards, 380px), *form* (560px), and *full* (the content width). Today `.card-wide`, `.as-of`,
  `.summary-tiles` and `.account-header` each repeat 880px. They become tiers. The holdings
  pages and Overview keep 880px until LY-4 to LY-6 lay them out. Otherwise they would turn into
  stretched, stacked pages for a while, with a 1600px chart about 800px tall.
- **Full width for Transactions, Tickers and the two admin mapping tables.** Text columns
  (instrument, name, reference) take the spare width, and numeric columns stay tight. The
  truncated transaction reference can then show in full. The admin page's form card stays
  narrow, as agreed.
- **Account header** (heading, sub-nav, switcher) spans the full width on the account pages
  that become full-width. It must line up with the cards under it, so on Holdings it stays at
  880px until LY-5.
- Page gutters scale with the window (`clamp()`), so a 1920px screen gets a margin, not 24px.

Tests: the moved pages render their tables in a full-tier card. Verify with LY-1's checks: no
overflow at any width, phone stacking unchanged.

Files: `static/web.css`, `templates/{transactions,tickers,admin_mappings,base}.html`,
`partials/_account_header.html`, `docs/ui-architecture.md`, `screenshots/`.

Done as:
- `--content-max` is now the content's own width (1600px), gutters outside it. The header and
  the page share one width rule and one gutter (`--gutter: clamp(24px, 3vw, 48px)`, 12px at
  phone width), so the logo sits on the content edge at every width.
- Full width is set per page, not per element: `base.html`'s `page_width` block adds
  `page-full` to `<main>`. The account header, as-of line, tiles and `.card-wide` take
  `max-width: var(--page-wide)`, 880px by default and `none` on a full page, so the account
  header needs no parameter. The narrow and form tiers stay `.card-narrow` and `.card`.
- `/portfolio` became a full page, so `.content-wide` is gone already.
- Transactions: text columns take the spare width, and the reference shows whole above the
  stack breakpoint. With a 36-character UUID the table needs about 1150px of window; between
  600px and that it scrolls in a focusable `.table-scroll` region, as `/portfolio` does.
  `.table-scroll` became `position: relative`: the visually-hidden "Actions" header escaped
  the box and still scrolled the page at 768px.
- The two breakpoints (stack 600px, phone 480px) are named in a comment by `--content-max`.
- `review_pages.py`: 208 of 208, from 196.

### LY-3. Chart sized by its container

**Status:** done

`portfolio_chart.js` uses Chart.js's default 2:1 aspect ratio, so its height is decided by its
width. The chart is 880px wide today, so about 440px tall. That breaks either way once layouts
change. Full width (stacked, about 1550px) makes it about 775px tall. Beside a table its height
is unrelated to the table's.

- Set `maintainAspectRatio: false`, and let a wrapper around the `<canvas>` set the height. By
  default that is a `clamp()` between a readable minimum (about 280px) and maximum (about
  480px). A layout can override it; LY-4 has the chart stretch to its row.
- Check that x-axis tick density and the range buttons still read well at about 420px wide (the
  chart column) and at phone width.
- On its own this changes nothing visible on today's 880px cards, apart from the height
  `clamp()` gives. So it can merge before LY-4.

Tests: the chart template renders the sizing wrapper on both pages that have a chart. Verify
visually at all widths, in all three chart views.

Done as:
- The wrapper is `.chart-frame`. Its height is `aspect-ratio: 2 / 1` of its own width, held
  between 280px and 480px, rather than a `clamp()` on the viewport. A chart in LY-4's column is
  much narrower than the window, so a viewport-based height would be wrong there. On an 880px
  card the height is unchanged (408px). Phone charts grow from about 150px to 280px, and
  `/portfolio`'s 1008px chart goes from 504px to the 480px cap.
- `review_pages.py` gains a market-value page for both chart pages, so all three views are
  reviewed, and a browser check that the canvas fills its frame and is 280 to 480px tall
  (`CHART_HEIGHT`). It fails at phone width if `maintainAspectRatio: false` is removed.
- Ticks and range buttons needed no change at about 420px wide or at phone width.

Files: `static/portfolio_chart.js`, `static/web.css`,
`templates/{portfolio,account_holdings}.html`, `docs/ui-architecture.md` (chart section).

### LY-4. `/portfolio`: table and chart side by side

**Status:** done

- **Layout.** The as-of line and the five tiles span the full width, so all five tiles fit on
  one row at 1280px and up. Below them is a two-column grid (`.split`, reused by LY-5 and LY-6).
  The Holdings card is on the left, about 3fr, with a minimum of the table's natural width. The
  Returns card is on the right, about 2fr, with a minimum of about 420px.
- **Breakpoint.** The ten-column table needs about 875px inside its card, about 940px with
  padding. With the chart's minimum and the gap, two columns need content of about 1400px, so a
  viewport of about 1460px or more. Below that the cards stack, table first, at full width. At
  1280px that is still a clear gain, 1232px against today's 880px. Measure the exact value in
  this task. The table keeps all ten columns (see Agreed requirements).
- **Tall tables.** Opening a holding's breakdown (`<details>`) makes the table much taller than
  the chart. The Returns card is `position: sticky` in its column, so the chart stays in view
  while the user scrolls the table. With both cards aligned to the top, the chart doesn't
  stretch with the table.
- **Error and empty states** still sit in their own card (holdings error with chart, chart
  error with holdings, first run). The first-run welcome card stays narrow and alone.
- **Chart height.** Override `.chart-frame`'s `aspect-ratio` and height bounds for this layout,
  and move `review_pages.py`'s `CHART_HEIGHT` to match. (First written as "stretch the chart to
  its row", which contradicts the top-aligned sticky card above; agreed instead: 4:3 of the
  column's width, 320–560px.)
- The `.table-scroll` wrapper is reviewed. The scroll box stays only for widths where the table
  still can't fit. (`.content-wide` went in LY-2, when `/portfolio` became a `page-full` page.)

Tests: both cards render inside the split container, holdings first. Each error state still
renders the other card. First run renders neither.

Files: `templates/portfolio.html`, `static/web.css`, `docs/ui-architecture.md`, `screenshots/`.

Done as:
- Breakpoint measured: the table is 875px, so its column's minimum is 940px with padding, and
  the chart's 420px. Two columns from a 1480px window (about 1473px needed); 3:2 above that,
  about 946px and 630px at 1920px. The added laptop width (1440px) reviews the stacked side.
- The stacked `.split` column is explicitly `minmax(0, 1fr)`. Left `auto`, it grew to the
  table's width and phone and tablet scrolled sideways.
- The Returns card (`.split-aside`) is sticky only in a window at least 640px tall (a new
  "tall" breakpoint): the card is about 580px at most, and a shorter window would hide its
  bottom.
- Chart in the column: `aspect-ratio: 4 / 3`, 320–560px, about 470px at 1920px.
  `CHART_HEIGHT` is now 280–560px.
- `review_pages.py` gains a check that no `.table-scroll` scrolls in a two-column `.split`.
  260 of 260, from 208, the laptop width included.
- `.table-scroll` stays: it scrolls only stacked, between the stack breakpoint and about
  1000px.

### LY-5. Account holdings: table and chart side by side

**Status:** done

The same `.split` on `/accounts/holdings`, after LY-4 has settled the pattern. Its table has
seven cells, not ten, so the two-column breakpoint is lower. Measure it; it is expected to be
about 1200px, so two columns already apply at 1280px. The account header, as-of line and tiles
span both columns. The "Prices loading" row and the no-history state behave as on `/portfolio`.
`.split`'s 940px table-column minimum and 1480px breakpoint were measured for `/portfolio`'s
ten columns (LY-4), so this page needs its own values, e.g. a modifier on `.split`.

Tests: as LY-4, for this page.

Files: `templates/account_holdings.html`, `static/web.css`, `docs/ui-architecture.md`,
`screenshots/`.

Done as:
- Breakpoint measured: the table is 640px, so its column's minimum is 704px with padding, and
  the chart's stays 420px. Two columns from a 1240px window (about 1221px needed, plus room for
  a classic 15px scrollbar), so 1280px is two columns, about 708px and 472px. 3:2 above that,
  as on `/portfolio`.
- The page became a `page-full` page, so the account header, as-of line and tiles span both
  columns. The 880px interim now covers only Overview and the account form pages.
- The container is `split split-account`. The modifier's rules come after `.split`'s, so from
  1480px its minimum wins. The 4:3 chart frame and the sticky aside are repeated in its own
  queries, because a breakpoint can't be a custom property.
- No `.table-scroll` box: stacked, the table fits its full-width card at every width above
  the stack breakpoint.
- `test_responsive_a11y.py` no longer lists the page among those keeping the 880px column.
  `review_pages.py` needed no change: 260 of 260.

### LY-6. Overview: two-column layout

**Status:** done

Today Overview shows four tiles and a two-line accounts list, and nothing else.

- The tiles span the full width.
- The accounts list is on the left and the cross-account returns chart on the right, in LY-4's
  `.split`. The chart uses the same `portfolio_chart.js` and the same `list_portfolio_returns`
  call that `/portfolio` already makes, so it needs no portfolio change. It is the existing chart,
  not a new figure. Two alternatives were considered: a "largest holdings" preview, and a
  one-column page with a wider accounts table. The chart was chosen.
- A failed returns call must not hide the accounts list, and the reverse. It is the same
  per-section error isolation `/portfolio` uses.

Tests: Overview renders the chart and accounts list in the split container. Each failing call
leaves the other section rendered. The first run shows the welcome card only.

Files: `api/overview.py`, `templates/home.html`, `static/web.css`, `docs/ui-architecture.md`,
`screenshots/`.

Done as:
- The chart's buttons, frame, island and scripts were copied in `/portfolio` and
  `/accounts/holdings`, so they became one macro, `partials/_returns_chart.html`, before
  Overview used it (its own commit, no visible change).
- Columns agreed as 2:3, the chart wider, through a `.split-overview` modifier. `.split`'s
  values stay `/portfolio`'s. The accounts column's minimum is 450px, the longest account row on
  one line plus padding (measured); with the chart's 420px, two columns from a 980px window (a
  new "overview-split" breakpoint, with room for a classic 15px scrollbar as in LY-5), so
  desktop, laptop and wide are two columns. No sticky chart: the list is shorter than it.
- Four tiles, as before. The growth tile was considered and left out.
- An account-list failure is now a banner inside the Accounts card with the chart still drawn,
  rather than a banner alone on the page.
- `review_pages.py` gains Overview's market-value and growth views: 280 of 280, from 260.

### LY-7. Retire the 880px column

**Status:** done

Once every data page uses a width tier, remove what is left of the old column: the 880px
default of `--page-wide` (full becomes the default, so the `page_width` block may go too) and
`.card-wide` if nothing still needs it. `.content-wide` and the per-block `max-width: 880px`
rules already went in LY-2. Run a final `review_pages.py` sweep at
every width, and check that `ui-architecture.md` no longer describes the 880px layout anywhere.

Files: `static/web.css`, any template still using the old classes, `docs/ui-architecture.md`,
`screenshots/`.

Done as:
- `--page-wide`, `.page-full` and `base.html`'s `page_width` block are gone, and so is every
  `page-full` in the six templates that set it. The account header, as-of line and tiles are
  the content width on every page.
- `.card-wide` stays as the full tier (`max-width: none`): it is what lifts `.card`'s 560px.
- The last 880px block was the account header on Add, Import and the two delete confirmations.
  It now spans the page as on Holdings and Transactions, so the sub-nav no longer moves between
  tabs. Agreed: the form card under it starts at its left edge (`.account-header ~ .card`), not
  centred under a 1600px header. Other form and narrow pages stay centred.
- `review_pages.py` needed no change: 280 of 280.

## Deferred

Carried over from the earlier chunks, each needing a new `portfolio` endpoint (and possibly a
schema discussion per `.claude/rules/data-schema.md`):

- Editing or deleting a transaction.
- Renaming or deleting an account.
- Pagination or filtering of transactions, if `GET /transactions` doesn't already support it.
- A list of the currencies `data-store` tracks (its `currencies.yaml`), so manual entry can
  suggest exactly those (UI-9 suggests a fixed list instead).
- Unmapped instruments per account, not per user (UI-7 filters by broker, so two accounts at one
  broker share a list).
- Rescaling prices quoted in minor currency units (`GBp`, `ZAc`, `ILA`).
- Merging different listings of one security (e.g. by ISIN) into one portfolio row.

New in this chunk:

- Forms beside the list they add to (Accounts, Add transaction, Add a ticker, the admin mapping
  form), on wide screens. Left out of this chunk by choice.
