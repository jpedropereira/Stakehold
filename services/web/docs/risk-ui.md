# web Risk UI

The `/risk` page's design decisions: its sections, their tables, the heatmap, the concentration
tables, the rolling charts, the regime tables, the what-if and the candidate ranking. Split out of [`ui-architecture.md`](ui-architecture.md) for size
before RK-25, verbatim apart from this header; RK-25's, RK-20's, RK-28's and RK-29's entries were added here. It is part of
the same document, so
`.claude/rules/architecture.md` applies to it: a change to anything described here updates this
file in the same change. The server side of `/risk` (its router, the upstream calls) is in
[`architecture.md`](architecture.md) and [`endpoints.md`](endpoints.md).

## Key decisions

- **Sub-pages under one sub-nav (UX-6).** `/risk` held five sections and was about 12,500px tall
  on a desktop, and every view loaded all of them. It is now four routes: `/risk` (ex-post),
  `/risk/ex-ante`, `/risk/what-if` (the what-if and the ranking, which both add to today's mix)
  and `/risk/concentration`. Each loads only its own sections, can be linked and bookmarked,
  and is headed by `partials/_risk_header.html`: the title, the glossary link and the sub-nav,
  whose current link carries `aria-current="page"` (the account sub-nav's pattern and styles).
  *Rejected: collapsible sections on one URL*, which still load every section's data, *and JS
  tabs*, which need a separate no-JS fallback. Decisions (agreed):
  - *The sub-nav carries the whole query string.* Every control's state survives a trip
    between sub-pages, as it did on one page: a what-if candidate is still there after a look
    at Ex-ante. One rule, so there are no per-page lists to drift. *Known cost:* a URL can
    carry parameters its own page ignores. *Rejected: carrying only the benchmark*, which
    would put the what-if back on 3Y with no exclusions, so its "before" could differ from the
    ex-ante figures just seen.
  - *The what-if page names ex-ante's window and exclusions and links there to change them*
    ("Over the ex-ante window, 5Y, excluding TSLA. Change it on Ex-ante."). It keeps one
    control per parameter, as RK-20 decided. *Rejected: repeating the window switch*, a second
    copy of one control and a second UX-10 swap target.
  - *The benchmark field is on both ex-post and ex-ante*, since ex-ante sends the benchmark too
    (the heatmap's row, the regimes, the pair). It is one `?benchmark=`, so the two can't
    disagree.
  - *Section ids are unchanged*, so a link's `#anchor` still lands on its section. A link to
    an old in-page section (`/risk#risk-ranking`) reaches `/risk`, since a fragment never
    reaches the server, and `static/risk_anchor.js` sends it on to its sub-page with its query
    and anchor (`location.replace`, so Back doesn't bounce). *Known cost:* without JS it lands
    on ex-post, with the sub-nav one click away.
  - *Copy that said "above"* now names the other page or drops the word. UX-9 does the full
    copy pass.
- **Risk page (RK-12): one table per section, measures down and windows across.** `/risk`'s
  first section, now its own sub-page (UX-6), is the ex-post summary: twelve measures × five windows (1Y, 3Y, 5Y, 10Y, Max).
  Measures are the rows, because there are more of them than windows and their labels are long.
  At phone width, then, each measure stacks into a block of five labelled figures (`.stack-table`),
  as a Labs row does. Between that and the width all six columns fit, it scrolls in its own
  `.table-scroll` box. Each section has its own loader in `api/risk.py`, so a failure shows a notice in
  its section and the rest of the page still renders.
  - **Every figure's metadata (spec §10).** The window is in its column header, with its
    dates, and the frequency (daily or weekly) is under each measure's label. The label
    "ex-post · invested assets" is the section's heading. The exact figures (the window a
    weekly figure was shrunk to, n_obs and gaps) are in the cell's `title`. *Known cost:* a
    `title` doesn't show on touch screens, which is why the glossary rejected it. Here it
    only adds detail that the visible headers already summarise. *Rejected:* a button per
    figure that opens its metadata (60 buttons), and an n_obs line under each header (VaR's
    masked day count differs from the daily one, so it would need its own note).
  - **Colour only on returns, and Labs' formats.** Cumulative return and CAGR are signed, to 1
    decimal and coloured by sign (`signed_pct1`, `delta_class(..., "pct1")`), as the Labs risk
    panel shows CAGR. Volatility, downside deviation, max drawdown and VaR/CVaR are `pct1` and
    the ratios `ratio`, all in ink, because forest and oxblood mean a gain or a loss and these
    are neither. A max drawdown's `−` is its sign, not a loss colour, as in RK-9.
  - **Greyed, never blank (R9).** A figure without a value shows its reason in the caption
    tone. In a window the history doesn't cover, the header says why once, and each cell says
    "Not covered" rather than repeating the sentence twelve times. The "Measured from … to …"
    line above the table gives the history's start, which a phone, with no headers, still
    shows.
  - **Flags: numbered markers and a legend.** Each flag present gets a number, in a fixed order
    (`risk_view.FLAG_NOTES`), and the legend under the table explains only those present, in a
    sentence each. `insufficient_history` gets no marker, because the greyed cell already says
    it. A flag web doesn't know yet is shown as "Flagged: <name>" and logged, never dropped.
    *Known difference:* the Labs risk panel (RK-9) words flags as short notes under each figure
    (`labs_risk_view._FLAG_NOTES`), so the two pages keep separate wordings. Each design was
    agreed for its own table; sharing one wording is a possible follow-up.
  - **A figure, its s.e. and its markers are one `.risk-value` span.** A stacked row lays out
    its cell with flex. Loose, the marker and the "± s.e." were separate flex items, pushed to
    the far edge away from the figure.
  - **Copy says "historical" (R15).** The section's subtitle, and every risk glossary entry,
    describe what happened over the window, never what to expect.
  - **Against the benchmark (RK-14).** A GET form above the table (`partials/_benchmark_field.html`,
    shared with `/labs`) picks the benchmark, `?benchmark=`, `VWCE.DE` by default. The choice is
    the URL, never stored, since saving it needs a settings schema (deferred). The field suggests
    the tracked tickers from a `<datalist>`, but takes any ticker: portfolio greys an untracked
    one with its reason, so web doesn't check it. Only an over-long value is caught, and it shows
    an inline error in the charter's ink style. The four figures (beta, correlation, tracking
    error, information ratio, all weekly) are a second `<tbody>` under the measures, headed by
    the benchmark and its first date (spec §5.6 asks for both wherever relative figures appear).
    They are in ink, because none is a gain or a loss. When the benchmark is unavailable, the
    heading says why once and each cell says "Unavailable", as an uncovered window's say "Not
    covered". Each cell's `title` ends "· against <ticker>". *Rejected: a separate table*, which
    would repeat the five window headers for four rows. *Rejected: checking the ticker against
    the list in web*, a second copy of portfolio's rule that could drift from it.
  - **Ex-ante · constant weights (RK-18).** The second section, now `/risk/ex-ante`: today's mix (the holdings at
    their market-value weights, `labs_view.build_default_mix`) held through one window, labelled
    apart from the ex-post figures (R2). `risk_ex_ante_view.py` builds it. Decisions (agreed):
    - *One column, the measured window.* The window links (`?ex_ante_window=`, 3Y by default,
      spec §7.1) rerun the page, as Labs' `?risk_window=` does, so it works without JS. The
      summary reuses the ex-post table's macro and cells. Its volatility row is σ_p, which equals
      the constant-weight weekly volatility exactly (spec §6.2), so the page shows one
      volatility for one mix; the diversification ratio sits under it, and the effective number
      of holdings is a line under the table (a plain number, with no window to report).
    - *The limiting holding (R12).* The "Measured from" line says which holding shortened the
      window and from when, with an "Exclude <ticker>" link per `limited_by`; an excluded
      holding gets an "Include … again" link. Both are `?exclude=` links built from the current
      query, and the benchmark form re-sends them as hidden inputs.
    - *Weight beside share of risk (spec §6.3).* The decomposition shows weight, share of risk
      (PCR), their ratio, volatility, contribution and correlation with the mix. A note above it
      names each holding whose share of risk is at least 1.5× its weight **and** 5 points above
      it ("TSLA is 10.0% of the money and 25.0% of the risk"), with a gold rule beside it, the
      accent's "look here", never oxblood. The 5 points keep small holdings out of it: a 1%
      holding at 2% of the risk is 2× but immaterial. A hedge (negative share) gets its own
      note. Under 104 weeks every figure is null; the table says why once and keeps the
      weights. *Rejected:* naming only the largest gap (hides a second one), and no note.
    - *The € figure (spec §5.5)* is the constant-weight 1-day VaR 95% and 99% ("1 in 20", "1 in
      100") on the market value of the holdings portfolio included, not the whole portfolio's:
      a holding left out isn't in the mix the percentage describes. *Rejected: the overview's
      total*, which would apply one mix's percentage to another.
    - *The heatmap is an ink ramp on ρ from 0 to 1.* Four tints of `--text` over the card
      (10/20/30/80%), the darkest with the card colour as text. ρ below 0.2, negative included,
      is unfilled, so dark always means "moved together"; the printed number carries the sign.
      *Rejected:* a ramp on |ρ|, which shades a hedge like a duplicate, and gold for positive
      with ink for negative, which spends the accent on decoration (and forest and oxblood are
      for gains and losses only). AA decides the steps: ink text holds 4.5:1 on a tint of at
      most 33% and flipped text needs at least 69%, in both themes, so there are three light
      steps and one dark one (ρ ≥ 0.8), not a smooth ramp. `test_brand_contrast.py` reads the
      percentages from `web.css`. Classes, not inline styles, because the CSP has no
      `'unsafe-inline'` for styles. Each square's `title` has its own pair window and n_obs
      (R13: pairwise, for display only); a greyed one reads "n/a". The benchmark is a row,
      marked as such, once even when it is also a holding.
    - *EUR only.* The request always sends `currency: eur`; spec §7.1's local-currency toggle
      wasn't built (agreed), since the EUR matrix is a euro investor's risk.
    - *Review tooling.* Chromium reports a `color-mix()` background as `color(srgb …)`, which
      `review_pages.py`'s contrast check didn't parse, so it measured the darkest squares
      against the card. It parses that form now.
    - *How much of the risk is the currency (RK-26, spec §6.4).* A table under the
      decomposition, one row per holding priced in another currency: the holding, its currency,
      then local, currency and euro volatility and the correlation of local and currency
      returns, in the spec's order. Each column header says what it measures ("against the
      euro", "what you held"), since "FX volatility" means little to an owner. It's built like
      the summary. Each cell is a `risk_view.figure_cell` with its window and n_obs in its
      `title` and its reason when greyed (1Y always greys the correlation, which needs 104
      weeks), and its flags join the section's legend. All four figures are in ink: a
      volatility or a correlation is neither a gain nor a loss. A fixed note above it says what
      the columns mean and that a negative correlation means the currency offset the holding's
      moves. It also says the rows use the same weeks as the share of risk, because portfolio
      puts them on the decomposition's panel (agreed), so the euro volatility repeats the
      decomposition's. The note doesn't repeat the dates, which the "Measured from" line above
      already gives. An all-EUR mix gets one sentence instead of an empty table. *Rejected (agreed):
      a derived "currency share of risk"*, which reads easily but doesn't add up, because the
      euro variance has a correlation term and a cross term. The table needs no CSS of its own:
      it's a `.risk-table.stack-table`, so a phone stacks each holding into labelled lines.
  - **Concentration · look-through (RK-23).** The third section, now `/risk/concentration`: portfolio's look-through
    (RK-22, spec §8) of the same default mix as ex-ante, built by `risk_concentration_view.py`.
    Decisions (agreed):
    - *The unmapped share comes first.* A fund with no holdings data can't be looked through,
      and every table would otherwise sum to 100% as if complete. So when unmapped is above 0,
      a note with the gold rule of RK-18's insight note (the accent's "look here") gives the
      share and names each fund with portfolio's reason. Under it is the coverage split
      (looked through, direct, commodity, unmapped), and each table has its unmapped funds as
      rows of their own.
    - *A table per dimension with an ink bar.* Each row's weight has a bar beside it, on a
      fixed 96px track for 0–100% of the mix, so bars compare down a table and across tables,
      and on a phone the figure and its bar stay together. The bar is inline SVG whose width is
      the rect's attribute: the CSP has no `'unsafe-inline'` for styles, and RK-18's ten-step
      classes would be too coarse here. The fill is a 45% ink tint (unknown and unmapped rows
      20%, their labels in the caption tone), never gold, forest or oxblood: an exposure is
      neither a gain nor a loss. The bar is `aria-hidden`, since the printed figure carries
      the value. *Rejected: Chart.js bars*, which need JS, a no-JS fallback and their own size
      checks; *tables only*, which make 67.8% USD read like any other row; *a bar spanning the
      cell*, which the review screenshots showed sitting apart from its right-aligned figure.
    - *One currency table, two columns (R16).* The rows are currencies. The columns are
      portfolio's labels: "Listing currency of holdings" (the look-through, the headline, with
      the bar) and "Trading currency of positions" (each held ticker's own). A currency only
      one view has is `0.0%` in the other: a real 0% of the mix, not a gap. Put together, a
      EUR investor sees that 67% USD exposure comes through 3% USD-traded positions.
      *Rejected: two tables*, where the same currency sits in two places.
    - *10 heaviest known buckets, the rest in "show all".* Unknown, unmapped and other buckets
      are always shown, because they say what the table can't see. The remaining known
      buckets, and names 11–25 of portfolio's top 25, are in a `<details>`, which needs no JS.
      Each table's concentration line gives the top 1/5/10 shares of the mix, then the
      effective number and Herfindahl over the known share (RK-22's rule), with that share
      named. With nothing known, the line says why instead (R9).
    - *Always the whole mix.* `?exclude=` exists to lengthen ex-ante's window (R12), and
      concentration has no window, so it isn't sent. The section shows the snapshot date
      ("IWDA.AS's holdings as of …", data-store's scrape date) because look-through is a
      point-in-time view.
    - *Glossary.* There are new entries for look-through, the listing currency of holdings
      (a proxy, not revenue) and the effective number (Herfindahl). The last notes that, unlike
      the effective number of holdings, it looks through the funds.
    - *Review tooling.* The review holdings hold no fund to look through, so
      `review_fakes.py` answers with data shaped like the RK-22 dev run instead. A
      `risk-concentration-all` page opens every "show all".
  - **Rolling charts (RK-25).** RK-24's rolling figures, drawn in the sections they come from:
    ex-post's rolling 1Y volatility and Sharpe under its table, measured on the TWR, and
    ex-ante's on the constant-weight mix after the heatmap, followed by the rolling correlation
    of a pair. `risk_rolling_view.py` decides what is shown, `partials/_rolling_charts.html`
    lays it out, and `static/risk_rolling_chart.js` draws every canvas from its data island.
    Decisions (agreed):
    - *In each section, not a section of their own.* The charts come with each section's own
      call, so a failed call takes only its own charts with it, and R2's two labels never
      share a chart. *Rejected: a "Rolling" section*, which would put ex-post and ex-ante
      figures side by side and lose half its content whenever ex-ante failed.
    - *Two stacked charts, Sharpe with a ± 1 s.e. band.* Volatility is a percentage and Sharpe
      a ratio, so each has its own chart and axis. Stacked, their date axes line up. The band
      is each point's standard error around it, a 16% tint of `--text-muted` (the drawdown's
      fill, through `chart_theme.js`'s `translucent`). A 1Y Sharpe's SE is about 1, so the
      band shows how little one year's Sharpe says. *Rejected: one chart with two axes*,
      which invites reading a crossing as meaningful. *Rejected: the SE in the tooltip only*,
      where the noise is invisible.
    - *Ink only, as RK-24 asked.* Every line is `--text`. A volatility, a Sharpe or a
      correlation is not a gain or a loss, so there is no forest or oxblood, and gold stays out
      for the growth chart's reason. The zero gridline is `--text-muted`. ρ's axis is fixed at
      −1 to 1, so a move from 0.90 to 0.95 reads as small as it is. Volatility's axis starts
      at 0.
    - *The pair is named beside its chart, not in it (UX-3).* The chart's one line is
      "Correlation", so the canvas has no ticker to name. The note above it prints both sides
      through the `ticker` macro, and each option of the pair's two selects reads
      `AAPL — Apple Inc.`, with `(benchmark)` after the name for a benchmark outside the mix.
      A select can't hold the tooltip, so the name is the option's text; its value is still
      the bare ticker. A select is as wide as its longest option, so the pair's are capped at
      their row: at phone width the closed select cuts a long name and the open list shows it
      whole. The volatility and Sharpe charts are of the whole portfolio and name no ticker.
    - *A gap breaks the line, and its reason goes under the chart.* Each point is plotted at
      its window's end. A greyed point stays in the series as a null (`spanGaps: false`), and
      the canvas can't show why, so each run of greyed windows with one reason becomes one
      note: "No point for the 2 windows ending … to …: insufficient history (have 51, need
      52)." A series greyed throughout (Sharpe with no risk-free series) has its note and no
      chart, and the intro drops its sentence about the band.
    - *A summary in words* under each chart gives the latest measured window, the lowest and
      the highest, each with its end date. It stands in for the chart with no JS or a screen
      reader, and the canvas's `aria-label` names what is drawn.
    - *The default pair is the heaviest holding against the portfolio* (spec §7.1's "one asset
      against the portfolio"). It is defined whenever anything is measured, even with one
      holding. A GET form (`?corr_a=&corr_b=`, anchored at the chart) picks a holding from the
      mix as sent, or the benchmark, against the portfolio, another holding or the benchmark.
      It re-sends the rest of the query, and the benchmark form and the window and exclude
      links keep the pair. *Rejected: the heaviest holding against the benchmark*, which is
      empty when the benchmark is unavailable. *Rejected: the two heaviest holdings*, which
      needs two.
    - *A pair portfolio would reject falls back to the default.* Portfolio 422s a pair naming a
      ticker outside the mix and the benchmark, an excluded one, or the same ticker twice, and
      a 422 would cost the whole ex-ante section. So `chosen_pair` applies portfolio's rule
      first. A link from before a sale or an exclusion gets the default, a note saying which
      pair wasn't used, and a log line, as a stale `?exclude=` does. A pair portfolio accepts
      but can't measure (a side left out, an unavailable benchmark) shows portfolio's reason
      in place of the chart, with the picker still there.
    - *Sharp edges.* If the heaviest holding can't be measured (say, no FX series), the default
      pair shows a reason rather than a chart: web can't know before the call, and the picker
      is the way out. Ex-ante's rolling figures cover the mix's whole history, so
      `?ex_ante_window=` doesn't change them, though `?exclude=` does.
    - *Glossary.* There are new entries for the rolling window and the rolling correlation,
      the latter with its noise (about ±0.14) and its lesson, that correlations rise in
      sell-offs.
    - *Review tooling.* `review_fakes.py` generates the rolling points in code: ex-post with
      two greyed months, ex-ante over the mix's history, a weekly pair with crisis-like spikes
      and a gap, and 5Y answering with no risk-free series. There are six `risk-rolling*`
      pages, covering the default pair, a ticker pair, an untracked benchmark side and a stale
      link.
  - **What if · adding a candidate (RK-20).** The section after ex-ante, now on `/risk/what-if`: today's mix before
    and after adding a candidate (spec §7.2), from portfolio's what-if (RK-19), built by
    `risk_what_if_view.py`. Decisions (agreed):
    - *A GET form, as LB-7's is.* It has a candidate ticker, a weight and a funding `<select>`,
      so it works without JS and its state is the URL. The weight is a % of the mix *after*
      the change, as portfolio takes it; the label says so. Funding is one select: "Pro-rata
      from every holding", then "Replace X" for each holding left in after the exclusions.
      *Rejected: radios plus a holdings select*, where "replace" could be checked with no
      holding chosen. Each form re-sends the others' fields, and the ex-ante window and
      exclude links keep the what-if's, so changing one control changes nothing else.
    - *Ex-ante's window and exclusions.* `?ex_ante_window=` and `?exclude=` are sent as
      ex-ante sends them, so "before" is exactly the mix the ex-ante page measures. *Rejected:
      a window of its own*, a second set of window links whose "before" could disagree with
      the ex-ante figures beside it.
    - *Checked for what portfolio would 422, and no more.* That means an empty or over-long
      ticker, a ticker already in the mix, a weight outside (0%, 100%), and a funding holding
      that isn't in the mix sent. A form with errors makes no call and shows each error inline
      in the charter's ink style, as the benchmark field does. The tracked list is only the
      datalist's suggestions: a candidate priced only through a fund's holdings is valid (R14),
      and one portfolio can't measure gets a 200 that says why. A "replace X" choice made
      before X was excluded is an error, not a silent switch to pro-rata, which would answer a
      question the user didn't ask. Stale `?exclude=` values are dropped silently (RK-18), but
      dropping one doesn't change what's being compared.
    - *The table: Before, After, Change, risk first.* Rows come in portfolio's order:
      volatility, diversification ratio, the candidate's share of risk and its correlation with
      the mix (no before, so "—"), Sharpe ± 1 s.e., then max drawdown. Cells are the summary's
      (`risk_view.figure_cell`), so greyed reasons, titles and markers read alike. A change is
      in points for percentages (`signed_points1`, "−0.2 pts") and signed to 2 decimals for
      ratios, never coloured: a lower volatility isn't a gain. ΔSharpe carries its own s.e.,
      which is far smaller than either Sharpe's (RK-19).
    - *The threshold is a sentence, not a row.* It gives SR_c ± s.e. against ρ × SR_p, then a
      plain verdict ("would have raised it" / "wouldn't have"), with "historical" in the
      copy (R15). When it's greyed, the sentence gives the reason. Under replace funding it
      says it's shown under pro-rata only (portfolio's reason).
    - *Notes above the table.* R14's warning is a warning banner, in the spec's words, and
      every "after" figure carries the flag's marker. The flag's legend note was reworded for
      this case: only the what-if sets it. A shortened window says that the candidate's
      history set it and that "before" was measured over the same weeks. A capped replace
      names the weight the candidate actually got.
    - *States.* "Nothing to compare: …" gives portfolio's reason (the candidate, the holding to
      replace, or no holding measured). A failed call keeps the form, with a notice under it,
      so the user can retry. With no holdings, or a failed holdings call, the form isn't shown,
      since there's no mix to compare against.
    - *Not shown:* the before/after weights of every holding. Pro-rata scales them all evenly,
      and the one surprise (a capped replace) has its note.
  - **What if · ranking the tracked tickers (RK-29).** The section after the what-if, on the same sub-page: every
    tracked ticker the user doesn't hold, scored as a pro-rata what-if candidate at one weight
    (portfolio's `POST /risk/what-if/ranking`), on the ex-ante window and exclusions, so each
    candidate joins the mix that section measures.
    - *On demand.* A GET form with one field, the weight (5% shown until it's sent). Nothing is
      sent until it is, because the ranking costs about two data-store calls per tracked
      ticker, up to 50. The field hint says it can take a few seconds. On a 1Y ex-ante window
      the form says why it can't rank (the diversification ratio needs 104 weeks) and sends
      nothing, rather than letting portfolio's 422 show as a failed section.
    - *The order is a link, not a field.* An "Order by" segmented control (UX-5) above the note
      switches between Δ volatility (the default) and Δ diversification ratio; until UX-5 the
      two links sat inside the note's sentence. The current one carries `aria-current`, and the
      note names it. The note says why the Sharpe change is shown but never an order: the
      best of many noisy Sharpe changes is mostly luck (spec §7.2). There is no Sharpe link to
      tempt anyone.
    - *One row per candidate, numbered in portfolio's order.* Columns: rank, ticker over its
      tracked name, history (first day over its week count), Δ volatility, Δ diversification
      ratio, share of risk, correlation with the mix, ΔSharpe ± 1 s.e. Changes use the
      what-if's filters, never coloured. Each cell's `title` is its after figure's metadata.
      Greyed rows (a key under its minimum because of missing weeks) follow, unnumbered, with
      the reason in the greyed cells and a note under the table. *Rejected: a before column
      per measure*, which would double the width for numbers the what-if already shows for any
      one candidate.
    - *What wasn't ranked is named.* A "Not ranked" list gives each candidate's reason (too
      young for the window, with its history start, or the fetch's "no FX series for HKD"),
      and a note names the tracked tickers past the cap. Nothing is dropped silently.
    - *Every form keeps the others.* The ranking form re-sends every other control and its own
      order. The benchmark and what-if forms re-send `rank_weight` and `rank_by`, and the
      ex-ante links keep the whole query, so changing one section never clears the ranking, on any sub-page.
- **Regime tables (RK-28): one per section, grouped by dimension.** portfolio serves each
  section's `regimes`: ex-post's on the TWR and ex-ante's on today's mix over its whole history.
  `risk_regime_view.py` turns each into a "Market regimes" block, after ex-post's rolling charts
  and after ex-ante's pair. Two blocks, not one with both series, so R2's labels never share a
  table, and each fails with its own section.
  - *Grouped by dimension, every regime shown* (agreed). Each dimension is its own small table
    with a plain heading (Drawdowns, Recessions, Events, Monetary policy, ...), in the order its
    first regime starts. An unknown dimension is headed by its own name, not dropped. *Rejected:
    a dimension filter*, and *showing only the "stress" dimensions*, which would hide the
    monetary-policy and inflation eras.
  - *Columns:* the regime (name, scope · severity, its own dates, and "measured from … to …"
    when the series covers only part of it; "ongoing" for no end), then the series' return
    and max drawdown, then the benchmark's. The series' header is "Your holdings" or "Today's
    mix". A return is a gain or a loss, coloured; a drawdown is ink, as on the summary. Above the
    stack breakpoint the tables use fixed column widths, so the figures line up from one group
    to the next. At phone width each regime stacks into labelled figures.
  - *Cells are the summary's* (`risk_view.figure_cell`). The title gives the regime's measured
    window, frequency, n_obs, gaps and the section's label; the benchmark's ends "· against
    <ticker>". A short regime's drawdown carries `short_regime`'s numbered marker, with its
    note in the block's own legend. Almost every regime is under 200 days, so most drawdowns
    carry it. That is the agreed honest default (spec §4.5 "Amended (RK-28)").
  - *Benchmark:* the note under the heading names it and its first date. When it can't be used
    at all, the note says why once and each benchmark cell says "Unavailable", as on the
    summary. A regime it doesn't cover shows its own reason ("benchmark history too short (from
    …)").
  - *States:* "No market regimes are loaded yet", "Couldn't load the market regimes", or "No
    market regime overlaps this history", each in place of the tables. No view (nothing
    measured) means no block.
  - *Each dimension is a group closed by default* (agreed after RK-28's first build), a
    `<details>` whose summary is its heading and its count ("Events 4 regimes"). With real
    data, ex-ante's Max can overlap 60+ regimes, which open tables would turn into a page of
    their own. The legend stays outside the groups, so it shows while they are closed. No JS.
    *Rejected: opening the first group*, which would favour one dimension for no reason.
- **The ex-ante switches and the ranking's order swap their own section (UX-10).** On
  `/risk/ex-ante` the window and the Exclude/Include links swap `#risk-ex-ante`
  (`partials/_risk_ex_ante_section.html`). After Exclude X, focus lands on "Include X again".
  On `/risk/what-if` the order swaps `#risk-ranking` (`partials/_risk_ranking_section.html`).
  That reruns the what-if too, as the full page did. Out of band, each swap also sends the
  sub-nav (`partials/_risk_subnav.html`), whose links carry the whole query. The ranking's swap
  also sends the window line (`partials/_risk_window_line.html`) and the what-if form's hidden
  inputs. The rolling-chart scripts now load on every `/risk/ex-ante` view: a window can bring a
  chart the first view didn't have. The pattern: "Section switches" in
  [`ui-architecture.md`](ui-architecture.md).
