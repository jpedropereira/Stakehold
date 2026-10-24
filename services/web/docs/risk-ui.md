# web risk UI

The `/risk` page's design decisions: its sections, their tables, controls and charts. Split out
of [`ui-architecture.md`](ui-architecture.md) for size before RK-20, verbatim apart from this
header. It is part of the same document, so `.claude/rules/architecture.md` applies to it: a
change to anything described here updates this file in the same change. The server side of
`/risk` (its router, the upstream calls) is in [`architecture.md`](architecture.md) and
[`endpoints.md`](endpoints.md).

## Key decisions

- **Risk page (RK-12): one table per section, measures down and windows across.** `/risk`'s
  first section is the ex-post summary: twelve measures × five windows (1Y, 3Y, 5Y, 10Y, Max).
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
  - **Ex-ante · constant weights (RK-18).** The second section: today's mix (the holdings at
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
  - **Concentration · look-through (RK-23).** The third section: portfolio's look-through
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
