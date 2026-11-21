# web Labs UI

The `/labs` page's design decisions: its tables, its mix form and its charts. Split out of
[`ui-architecture.md`](ui-architecture.md) for size before RK-9, verbatim apart from this header; RK-9's entry was added here. It is
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
- **Labs mix form (LB-7): a GET form, so the mix is the URL.** At the top of the page since
  UX-8 (below), "Edit the mix" has one row per asset (ticker and weight %), then an empty row that adds one. Nothing is
  stored, so a lab can be bookmarked or reloaded, and "Reset to my portfolio" is plain `/labs`.
  It works without JS: submitting reloads the page with the new query string, and a hidden
  `period` field keeps the chart's period (LB-8). Each row's Remove
  is a submit button whose value is the row's place, and the route answers it with a 303 to
  the mix that's left (`labs_mix.canonical_query`). Otherwise a reload would remove the next
  row as well. A visually hidden Update button comes first in the form, because Enter submits
  with the first submit button, which would otherwise be the first row's Remove. Removing every
  row leaves `?ticker=`, an empty edited mix that reads "Add an asset to the mix", not the default.
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
    *Reversed in UX-8*, which puts the form first and scrolls a long mix in its own box.

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
    in the segmented control every switch shares (UX-5, see `ui-architecture.md`).
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
  - *The tooltip and the checkboxes carry each line's full name (UX-3)*. The data island holds
    a `name` per asset and for the benchmark, `null` when data-store has none, so it carries
    only the names of lines it draws and the script looks nothing up. *Rejected: the page's
    whole `ticker_names` map in the island*, most of it for tickers the chart doesn't draw.
    - *Tooltip.* A line reads `VWCE.DE — Vanguard FTSE All-World: +7.07%`, the benchmark's
      with `(benchmark)` after the name. Chart.js doesn't wrap a tooltip line, and at phone
      width a fund's name is wider than the canvas, which would clip it. So the name is cut
      with `…` to the room the canvas leaves (`chart_theme.js`'s `fitText`), and left out
      when fewer than four characters would fit. The names are kept in a `Map` beside the
      datasets, not on them, for the frozen-animation reason below.
    - *Checkboxes.* Each label prints its ticker through the `ticker` macro with
      `focusable=false`: the box is the tab stop and the label opens the name.
    - *The end-of-line labels stay bare tickers.* Their room comes out of the plot's width, so
      a name there would take most of the plot at phone width.
    - *Known cost:* a disabled box can't take focus, so an asset with no line shows its name
      on hover only. The returns table above the chart names it too.
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
  - **Known sharp edge:** `formatPct` mirrors `formatting.pct`, a third copy of the display
    convention beside `portfolio_chart.js`'s `formatEur`, with no test tying them together.
    It lives in `chart_theme.js` (`StakeholdChartTheme.formatPct`, moved there before RK-9), so
    the growth and drawdown charts share one copy.
- **The Labs risk panel and drawdown chart (RK-9).** Under the drawdown chart since UX-8 (under
  the returns table before), a "Risk" section
  shows the backtest's `risk` section (RK-8, `labs_risk_view.py`); under the growth chart, the
  mix's drawdown over the same period.
  - *Two tables.* The first is the mix's grid: CAGR, volatility, max drawdown, Sharpe, Sortino
    and Calmar as rows, 1Y/3Y/5Y/10Y/Max as columns, as RK-9 lists them. Downside deviation,
    VaR and CVaR are parsed but not shown here; `/risk` (RK-12, RK-18) shows those. The second
    compares the mix and every asset over one window (agreed): portfolio measures every row on
    one common window (R10), which exists so assets can be compared side by side. The window
    is picked by links (`?risk_window=`, 1Y by default) rather than client-side tabs, so the
    page works without JS, like the period links. *Known cost:* every window is already in the
    response, but a link reruns the backtest: RK-8 measured 0.60 s for a 12-asset, 17-year
    synthetic worst case. *Rejected: a `<details>` per asset*, which makes comparing assets mean
    opening several. *Rejected: the mix only*, which would leave the assets' figures unused.
  - *Spec §10's metadata is shown, not hovered.* `title` tooltips were rejected for the
    glossary ("Glossary" in `ui-architecture.md`): they don't show on touch, and screen readers
    announce them inconsistently. So each window's start is under its column header (and in
    the stacked label, through the same `data-start` as Max's), each measure's frequency under
    its name (Sharpe's also says "± 1 s.e."), and a "Returns used" row or column gives the
    daily and weekly `n_obs` ("252 daily · 52 weekly"). Every figure in a window shares one
    daily and one weekly series, so the counts are said once per window, not once per figure.
    The label "buy & hold (drifting)" (R3) is in the section's subtitle, which also says the
    figures are historical, not a forecast (R15).
  - *Greyed, never 0 or blank (R9).* A greyed figure shows portfolio's reason in its cell, in
    muted text that may wrap (a sentence, not a figure). An uncovered window reads "Not enough
    history" in each cell, as the returns table does, and its reason ("limited by AAPL
    (history from 2021-11-01)") is said once under the grid, for every window that shares it.
    The common window's start and the asset that sets it are named above the grid. With no
    risk-free data, one note says Sharpe and Sortino can't be measured, and why.
  - *Flags are notes under the figure*, in plain words ("under 3 years", "over 2% of days
    missing"). `insufficient_history` gets none, since the reason already says it. An unknown
    flag shows its code and is logged: a code qualifies the figure better than silence.
  - *Formats (spec §10).* Volatility and drawdown as percentages to one decimal (`pct1`,
    unsigned unless negative), CAGR to one decimal with its sign (`signed_pct1`), ratios to
    two places with no "+" (`ratio`): a Sharpe of 0.86 isn't a gain. A Sharpe is "0.86 ± 0.59"
    with its standard error in muted mono. Only CAGR is gain/loss colored, because it is a
    return; `delta_class` gained a `pct1` kind so its color follows its one-decimal display.
    Volatility and the ratios are neutral, since neither is a gain or a loss.
  - *The drawdown isn't oxblood (agreed).* A drawdown is a distance below the mix's last high,
    not a realised loss, and every value is 0 or below, so oxblood would make the whole chart,
    and the max drawdown row, read as alarm, against chapter IV's "no panic on losses". The
    chart is a 2px `--text` line with a `--text-muted` fill from 0% down to it, and the 0%
    gridline is drawn in `--text-muted`, as the growth chart's is. The fill's 16% alpha is
    applied to the resolved token by `chart_theme.js`'s `translucent` (a canvas normalizes it
    to hex first; moved there from `labs_drawdown_chart.js` for RK-25's Sharpe band), so no new
    token is added. *Rejected: gold*, for the growth chart's reason (2.2:1 on
    parchment). *Rejected: an oxblood line*, as above. *Rejected: no fill*, which leaves a
    shallow drawdown hard to read against the 0% line.
  - *The drawdown chart follows the growth chart.* It is under the asset boxes, in the same
    section, so the period links drive both. Its dates are the growth series', and a gap date
    (R5) is null, drawn as a break (`spanGaps: false`), since RK-8 keeps it null rather than
    bridged. The y axis tops at 0% (a high). Its `suggestedMin` is −1%, so a mix that never fell
    below its high reads as a flat line on a small scale; Chart.js's default would have run the
    axis to −100%. The tick labels take as many decimals as the step needs, so a shallow
    drawdown doesn't print "0%" four times. It shares `formatPct` with the growth chart through
    `chart_theme.js` (moved there first), and the `.chart-frame`, so `review_pages.py`'s
    chart-size check covers it.
  - *Stacked at phone width* like the returns table (`.labs-table`, `.stack-table`). Each
    cell's figure, ± and notes sit in one `.risk-value`, so the stacked flex line keeps them
    together opposite the label instead of spreading them out. The "Returns used" row's top rule
    is drawn only above the stack breakpoint.
  - The review adds `/labs?risk_window=3Y` (a gap note), `/labs?risk_window=10Y` (an uncovered
    comparison), a mix with NEWCO.DE (the fake answers it with no risk-free data) and
    `/labs?period=Max` for the drawdown. The fake's risk section uses AAPL's late start, so 5Y
    and 10Y are uncovered on every Labs page.
- **The benchmark on `/labs` (RK-14).** RK-13 made the backtest measure the mix and each asset
  against a benchmark and price its growth line. `/labs` shows both.
  - *Its own form, in the risk section (agreed).* A GET form at the top of the section
    (`partials/_benchmark_field.html`, shared with `/risk`) takes `?benchmark=`, `VWCE.DE` by
    default. Unlike `?risk_window=`, a benchmark asks portfolio for a different backtest, so the
    form re-sends the rest of the query string as hidden inputs (`api/labs._benchmark_kept`),
    and the mix, period and window stay as they were. The default mix stays the default: with no
    `ticker` key, there's none to re-send. The period links, the risk window links, the mix
    form's hidden `benchmark` and remove's redirect all keep it. *Rejected: the field inside
    "Edit the mix"*, simpler, but far from the figures it changes and submitted with the
    mix's Update. The form shows only with a risk section, the only place its figures appear.
  - *Rows in the grid and columns in the comparison (agreed).* The grid gains a second
    `<tbody>` of four rows (beta, correlation, tracking error, information ratio), headed by
    the benchmark and its first date, as on `/risk`. The comparison gains four columns, since
    portfolio measures every asset against the benchmark too. Its benchmark is named once,
    in the line above the table, not under each column: "· vs IWDA.AS" under four headers
    pushed the table past laptop width. Greyed reasons in it got a 9em floor, since eleven
    figure columns squeezed one to a word per line. An unavailable benchmark reads as on
    `/risk`: the heading or the line above says why once, and each cell says "Unavailable".
    *Rejected: the mix grid only*, which leaves the per-asset figures portfolio computes unused.
  - *The benchmark line is dashed ink, on by default (agreed).* A 1.5px `--text` line,
    dashed `[6, 4]`, drawn under the mix and over every asset line, and named by its ticker at
    its last point like an asset's (the same label plugin). Its checkbox comes first, checked,
    "<ticker> (benchmark)". The tooltip lists it second, after the mix. LB-8's palette rule still
    holds: there is no colour to spare, gold is 2.2:1 on parchment, and forest and oxblood are
    the mix's gain and loss, so the dash is what tells the line apart. Ink, not muted, because
    it is the line the mix is read against. A raised asset line is ink too, but solid. With no
    line (the benchmark starts after the period, or is unavailable), its box is disabled, "No
    line", and a note above the chart gives portfolio's `growth_reason`. *Rejected: a dashed
    muted line*, which recedes with the asset lines. *Rejected: off by default*, which hides
    the comparison a benchmark exists for.
  - **Sharp edge, found in review:** a string property of our own on a Chart.js dataset (a
    `series` key) left a chart frozen mid-animation after a checkbox toggle, while boolean flags
    like `isMix` don't. The cause inside Chart.js wasn't pinned down. `labs_chart.js` derives each
    line's series from `isBenchmark` and `label` (`seriesOf`) instead. Don't add string keys to
    a dataset without re-running `labs-two-lines` in the review.
  - *No benchmark drawdown line.* portfolio returns no drawdown series for the benchmark, and
    RK-9's note left that line optional.
  - The review adds `/labs?benchmark=IWDA.AS&risk_window=3Y` (the relative columns),
    `?benchmark=IWDA.AS&period=Max` (the line, with and without AAPL's line on) and
    `?benchmark=NOPE.XX` (unavailable). The fake's VWCE.DE starts after Max's first day, so the
    default Max chart shows the no-line note.
- **The mix editor first (UX-8).** The form drives every figure on the page, but it sat at the
  bottom, after three tables and two charts. A page's main action now comes before the results
  it produces.
  - *Order, in the markup and on screen:* the intro card; a `.split-labs` row holding the
    editor card, then the returns card (headed "Returns"); then one card with the growth chart,
    its drawdown, the folded risk section, "Not backtested" and the Left-out notes. The holdings
    error and every empty state sit in the returns card, so it is never empty.
  - *Beside on a wide screen, stacked below it (breakpoint `labs-split`, 1600px).* The
    editor's minimum is 400px. The table's is about 1000px, the width at which this entry's
    LB-6 notes it stops scrolling, plus 64px of card padding. With the gap that is 1488px of
    content, about 1598px of window with 3vw gutters and a classic scrollbar. Measured on the
    review data the table needs 751px; with every period "Not enough history" it needs 1131px,
    so that rare table scrolls in its box beside the editor. So review width `wide` shows two
    columns and `laptop` stacks. Stacked, the editor keeps a form card's 560px cap.
  - *Copy names the mix, never a direction:* the editor is beside the table on one screen and
    above it on another, so "below" was wrong half the time. "Add an asset to the mix to
    backtest it", "Fix the marked rows in the mix", "or try a mix of your own".
  - *A long mix scrolls in its own box (agreed).* LB-7 rejected the form above the table
    because 50 rows would push the figures a screen down. `.labs-rows` holds about eight rows
    (34rem), then scrolls, with a hairline where it cuts. A box that starts with more than eight
    rows is a labelled, focusable region (`role="region"`, "Mix rows"), so a keyboard can
    scroll it; a short one adds no tab stop. `labs_form.js` scrolls an added row into view and
    marks the box a region once it overflows. *Known cost:* the first row's labels head the
    columns and scroll away in a long mix (every input keeps its accessible label), and a row's
    error can sit below the box's visible part. *Rejected: every row shown*, LB-7's cost.
    *Rejected: the editor folded in a `<details>`*, one more click for the page's main action.
  - *The risk section is folded (agreed, ahead of UX-7).* Its "Risk" heading and its
    `#labs-risk` anchor stay outside the fold, so the benchmark form's and the window links'
    anchor lands on them. A `<details>` summarised "By window and per asset, against VWCE.DE"
    holds everything else. It starts open when the reader chose something in it: a window
    other than 1Y, a benchmark other than the default, or a benchmark error
    (`api/labs._risk_open`). Not on the parameters' presence, since the mix form and the period
    links always carry both. The section has no chart, so a closed fold has no sizing problem.
    The summary's ticker hosts its name tooltip without a tab stop of its own
    (`focusable=false`), since a summary is already one. UX-7 moved it onto the shared fold
    (`partials/_fold.html`, `.fold labs-risk-fold`), so it looks like every other fold.
- **"Not backtested" and "Left out" are folded (UX-7)**, each under its heading and summarised
  by its count: "1 asset", "2 holdings". "Left out" gained an `<h2>` to fold under; its notes
  still say "Left out:", a repeat that UX-9's copy pass owns. *Not folded:* the notes under the
  returns table and the growth chart saying what a period's figures leave out. They qualify the
  figure right beside them, so they stay where they're read.
  - The review adds `labs-long-mix` (12 untracked rows, the server's region, an error under
    every row), `labs-long-mix-added` (nine rows added in place) and `labs-risk-open`.
