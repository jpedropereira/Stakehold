# web Labs UI

The `/labs` page's design decisions: its tables, its mix form and its charts. Split out of
[`ui-architecture.md`](ui-architecture.md) for size (before RK-9), verbatim apart from this header. It is
part of the same document, so `.claude/rules/architecture.md` applies to it: a change to anything
described here updates this file in the same change. The server side of `/labs` (its router, the
upstream calls) is in [`architecture.md`](architecture.md) and [`endpoints.md`](endpoints.md).

## Key decisions

- **Labs (LB-6): one table, the mix first, every period a column.** `/labs` backtests the
  user's current holdings at `/portfolio`'s weights. It has a "Labs" link in the nav after
  "Portfolio", since it's an analysis of the portfolio rather than of an account. The table has
  a row for the mix, set apart by a heavier rule and weight, then one row per asset in the
  order sent (largest weight first), with the weight `portfolio` actually used. 1M to Max are
  columns, so each row reads left to right as one asset's history. A period an asset doesn't
  cover reads "Not enough history" in muted text, never a dash or 0%, because no figure exists
  and a zero would be read as one. It stays on one line like a figure. Wrapping it was tried and
  made rows three lines tall while the table still scrolled. So between phone width and about
  1000px the table scrolls in its `.table-scroll` box, like `/portfolio`'s, and stacks below
  600px. Max's header carries the mix's start date ("from 2021-11-01"). Since LB-9 each
  asset's Max is its whole history, so an asset whose history starts later shows its own
  "from" date under its Max figure (LB-10); one that starts with the mix doesn't repeat the
  header. Stacked, the headers are hidden, so each Max cell carries its row's own start in a
  `data-start` attribute, its drawn label reads "Max from …", and the in-cell date is hidden.
  The mix's figures cover only the assets whose history covers the period (LB-9). Under the
  table, a muted note per run of periods names the rest: "From 3M to 5Y the mix leaves out
  BRK-B: not enough history" (`labs_view.mix_left_out_runs`). A run ends where the set
  changes, so each note says exactly which columns it qualifies. Assets `portfolio` couldn't backtest are listed under "Not backtested" with
  `portfolio`'s own reason. Holdings the page itself left out (no price yet, worth nothing, or
  beyond the 50-asset cap) are named in muted notes, one per reason, under everything else,
  since they qualify the figures without being part of them. Holdings and the backtest each
  fail on their own, and a backtest failure still shows the notes. No holding with a market
  value reads "Nothing to backtest yet" and links to Accounts, where transactions are added.
  The terms backtest, buy and hold, and Max link to new glossary entries; Max's anchor is
  `max-period` (renamed in LB-10 from `longest-shared-history`, which LB-9 made untrue). Gains and losses use
  `delta`, so forest and oxblood appear only on figures. *Rejected: periods as rows and assets
  as columns*, which reads better for two assets but not for fifty.
- **Labs mix form (LB-7): a GET form, so the mix is the URL.** Under the table and the chart,
  "Edit the mix" has one row per asset (ticker and weight %), then an empty row that adds one. Nothing is
  stored, so a lab can be bookmarked or reloaded, and "Reset to my portfolio" is plain `/labs`.
  It works without JS: submitting reloads the page with the new query string, and a hidden
  `period` field keeps the chart's period (LB-8). Each row's Remove
  is a submit button whose value is the row's place, and the route answers it with a 303 to
  the mix that's left (`labs_mix.canonical_query`). Otherwise a reload would remove the next
  row as well. A visually hidden Update button comes first in the form, because Enter submits
  with the first submit button, which would otherwise be the first row's Remove. Removing every
  row leaves `?ticker=`, an empty edited mix that reads "Add an asset below", not the default.
  - **Errors sit under their row.** The ink rule's bar and bold lead (the ticker, or
    "Weight") are used, never oxblood. Each error is tied to its input by `aria-describedby`
    and marks it `aria-invalid`. The errors are:
    - an untracked ticker, linking to "Add a ticker" with the symbol filled in,
    - a repeat of an earlier row,
    - a missing weight or one that isn't a positive number, with `%` and a decimal comma
      accepted,
    - more than 50 rows, for the form as a whole.
  - **Matching.** Tickers match the tracked list in any case and take its spelling.
  - **The last valid mix.** A mix with errors isn't sent. The table keeps showing the mix it
    last showed, which the form carries as hidden `last_ticker`/`last_weight` fields. A
    warning banner above the table says so and links to the form. A bookmarked URL with
    errors and no last mix shows only the form.
  - **Weights.** They are normalized. The form shows their total and, when it isn't 100%,
    says they're scaled, and the table's Weight column shows what was used. The default mix
    fills the form with its weights to two places, which can total 99.99%, or less than 100%
    past the cap.
  - **Without the ticker list.** If the list fails to load, the form keeps working with a
    notice and no suggestions, and `portfolio` names untracked tickers as "not tracked" in
    "Not backtested".
  - **Labels.** Only the first row's labels are drawn. The rest are visually hidden, so every
    input keeps its label.
  - **Phone width.** The weight column narrows, since a ticker needs the room.
  - **`labs_form.js`** unhides "Add another asset". It clones a `<template>` row, numbers its
    ids, and gives it a Remove that only takes it off the page.

  *Rejected:*
  - *Rejecting weights that don't total 100%.* The default mix's rounding would trip it, and
    scaling is what a reader expects of "60 and 60".
  - *Backtesting only the valid rows on an error.* The figures would belong to a mix nobody
    asked for, since a mistyped 50% asset would vanish and the rest would be re-weighted.
  - *The form above the table.* With 50 holdings, it would push the figures a screen down.

  *Known cost:* the URL carries the mix twice once it has been edited. Even at 50 assets, that
  is a few KB, well within what browsers and servers accept.
- **The Labs growth chart (LB-8): one period at a time, chosen on the server.** Under the
  table, `static/labs_chart.js` draws the cumulative % growth of the mix and its assets over
  one period, from a `#labs-chart-data` island (`labs_view.build_growth_payload`).
  - *Periods are links, not client-side buttons*: buy and hold re-bases each period on its own
    first day, so one long series can't be sliced like `/portfolio`'s ranges. `?period=` picks
    the series portfolio computes; 1Y is the default and stands in for anything that isn't a
    period. Each link is the current query string with only `period` changed, so it carries
    whatever mix the query string holds (LB-7). The current one is `aria-current="true"`,
    styled like the pressed chart buttons.
  - *Styling, agreed against chapter II*: the mix is the heavier line (2.5px), gain/loss
    colored by segment like `/portfolio`'s total return, since a growth is a gain or a loss.
    Assets are thin (1.25px) `--text-muted` lines; hovering or focusing an asset's box raises
    its line to `--text`, above the other assets and below the mix. The 0% gridline is drawn in
    `--text-muted`, stronger than the grid, since every line starts there. *Rejected: the mix in
    gold.* Gold is the one accent, and as a line on parchment it is 2.2:1, under WCAG 1.4.11's
    3:1 for a chart's marks (5.5:1 on dark). The checkboxes are tinted `--text` for the same
    reason: they are the app's first, and the browser's blue isn't in the palette.
  - *Asset lines start hidden, always*: a mix can have 50 assets, and 50 lines would bury the
    mix. Each asset has a checkbox under the chart (rendered `hidden`, since it does nothing
    without the script); one with no line for the period is disabled and says "Not enough
    history". The mix is always the main line: since LB-9 it has one whenever any asset covers
    the period, over just those assets, and a note above the chart names the ones it leaves out
    ("Over 5Y the mix leaves out AAPL: not enough history"). *Removed in LB-10:* LB-8 turned the
    covered assets on when the mix had no line. LB-9 made that state impossible, and on the dev
    stack it had drawn 11 identical muted lines. No mix line now means no line at all, which
    reads "No asset's history covers …" and draws no chart, keeping the period links.
  - *Asset lines are named, not colored (LB-10)*. The palette has no colors to spare, so each
    shown asset line carries its ticker at its last point, in `--font-mono`, drawn by a small
    plugin inside `labs_chart.js` (no new vendor file). Labels that would overlap are pushed
    apart vertically and kept inside the plot's height. A late asset's Max line is null before
    its first day, so its label goes at its last non-null point. The labels sit in right padding
    sized to the widest shown ticker, and are redrawn once the mono web font has loaded, since
    a canvas doesn't wait for it. Hovering within 12px of a shown asset line raises it, as
    hovering its box does. The tooltip has no color squares (`displayColors: false`), which
    matched no line, and lists the mix first, then only the shown lines. *Rejected: a color per
    asset*, which chapter II has no room for. *Rejected: a legend*, which would repeat the
    checkboxes under the chart.
  - *The date axis is capped by the plot's real width (LB-10)*. Chart.js's tick skipping sizes
    the dates against a width that leaves out the y axis, so with the labels' padding at phone
    width adjacent dates touched. `labs_chart.js` sets `maxTicksLimit` to the dates that fit
    with a 12px gap, on every redraw and on resize.
  - *Shared, not copied*: the theme colors and the redraw on `stakehold:themechange` and on a
    `prefers-color-scheme` change moved out of `portfolio_chart.js` into
    `static/chart_theme.js` (`window.StakeholdChartTheme`), loaded before each chart script;
    the frame is the same `.chart-frame`, so `review_pages.py`'s chart-size check covers it.
    The review adds `/labs?period=Max`, `/labs?period=5Y` (AAPL left out of the mix), `/labs`
    with an asset's line turned on, and `/labs?period=Max` with both asset lines on (AAPL's
    starts late, and the two labels are pushed apart). `review_pages.Page.click` takes a tuple
    of selectors for that.
  - *Payload size*: the island's figures are floats to six significant digits, the fewest
    that keep the tooltip's hundredths of a percent exact (up to 10,000%). That is only about
    30% shorter than `portfolio`'s eight-place strings: a synthetic 50-asset, five-year Max
    island came to about 645 KB, against 937 KB. **Known cost:** a large mix over a long
    period puts several hundred KB of JSON in the page. Only that period is sent, and 1Y, the
    default, is about a fifth of it. If it matters, thinning long periods to weekly points is
    the next step.
  - **Known sharp edge:** `labs_chart.js`'s `formatPct` mirrors `formatting.pct`, a third copy
    of the display convention beside `portfolio_chart.js`'s `formatEur`, with no test tying
    them together.
