# Risk roadmap, done tasks (archived)

The risk chunk's `tasks-risk-thread-done.md`, archived with [`risk.md`](risk.md), its thread
file. The content is verbatim except for relative links, adjusted for the new location.

---

# Tasks: risk chunk, done

The subsections of the risk chunk's done tasks, split out of
[`tasks-risk-thread.md`](risk.md) for size before RK-20 was marked done, verbatim
apart from this header. That file still holds the status table, the requirements and
constraints every task follows, the open tasks and the Deferred list; it is the thread file
`.claude/rules/tasks.md` means. A task's subsection moves here when it is marked done.

## Tasks

### RK-0. Roadmap housekeeping

**Status:** done

Move `dev/risk.md` to `docs/domain/risk.md`, the location it names. Amend it only where
Discovery made it false, and mark each amendment as such:
- §3 streams 3 and 3b don't exist yet. 3 is built in RK-10.
- §6.1 is resolved: invested assets only, on the union calendar.
- §9: EUR conversion stays in portfolio.
- §4.2: FX is from yfinance.

Link this file from the spec's header.

**As built:** `dev/` is gitignored, so the spec enters the repo with this move. Each amendment
is a note marked "Amended (RK-0)" beside the text it corrects, and the original text is kept.
The header notes the amendments.

### RK-1. data-store: split `architecture.md`

**Status:** done

At 63.7 KB, it has no room for RK-2, RK-21 or RK-27. Move a self-contained section verbatim to
a file of its own, as web did in LB-6. This is docs only.

**As built:** the whole "Read API" section moved, not just its endpoint table, which would have
freed only 2.3 KB for three new endpoints and an ingestion design. It is now
`docs/read-api.md` (10.6 KB), verbatim apart from its header and three "above"/"below" pointers
that now link back to `architecture.md`. `architecture.md` keeps a short "Read API" stub that
links to it, and is 54.2 KB. RK-3, RK-21 and RK-27's endpoints and their reasoning go in
`read-api.md`; RK-2's ingestion goes in `architecture.md`.

### RK-2. data-store: risk-free rate ingestion (€STR + EONIA)

**Status:** done

- **Schema first.** Propose a new table and agree it. `macro_indicator_rates` doesn't fit (its
  key has no series code, and `rate_value` is a YoY %). Recommended: store each published series
  raw, one row per (series, rate_date, source), and splice at read time in RK-3. Then the stored
  numbers stay checkable against the ECB, and the −0.085 pp spread is a single named constant.
- **Source:** the ECB Data Portal. The €STR series is probably `EST.B.EU000A2X2A25.WT` and EONIA
  `EON.D.EONIA_TO.RATE`. **CHECK** both against the portal before coding, and fetch a known
  published value to confirm the units.
- A new source module behind its own small Protocol, retries as `macro/retry.py` does, a CLI
  command, and a task in `daily_pipeline` (plus a backfill path). Negative rates are stored as
  they are (§4.1).

Tests: parsing a recorded portal response (unit). Upsert idempotency, and both series stored
(integration). Logging counts.

Done as specified, with these outcomes:
- **Both keys were confirmed** on 2026-10-20. The units are % per year, ACT/360.
- **The table is `risk_free_rates`:** `(series, rate_date, source)`, with `rate NUMERIC(8,4)`.
  The series are named `estr` and `eonia`.
- **There's no separate backfill command.** `ingest-risk-free-rates` fetches a series' whole
  history when none of it is stored, and skips EONIA (closed on 2021-12-31) once it's stored.
- **The retry loop moved to `provider_retry.py`.** A 4xx other than 429 from the ECB isn't
  retried.
- **For RK-3:** EONIA rows after 2019-09-30 are stored too, and are €STR + 0.085 by
  construction. The join has to take EONIA only up to 2019-09-30.

### RK-3. data-store: risk-free endpoint

**Status:** done

`GET /rates/risk-free?start&end` returns the spliced daily `rf_eur` (annualised %), one point
per published day, each with its source (`estr` / `eonia_adjusted`). It also returns the series'
first date, so that consumers can apply R4. It doesn't forward-fill: portfolio does that, under
§4.1's convention.

Tests: the splice boundary (2019-09-30 is EONIA − 0.085, 2019-10-01 is €STR), a window before
1999, and an empty range.

**As built:** `GET /rates/risk-free?start&end[&source=ecb]` →
`{source, first_date, points: [{date, rate, series}]}`.
- **The rule is `risk_free/splice.py`** (`ESTR_FIRST_DAY`, `EONIA_SPREAD`), a pure function. The
  date alone picks the series, so EONIA rows from 2019-10-01 on are never served, even on a day
  €STR is missing.
- **The per-point field is `series`** (`estr` / `eonia_adjusted`), not `source`, which is
  already the top-level provider field.
- **`rate` is a string at four places**, like every other read model.
- **`first_date` covers the whole series, not the window**, so a window entirely before 1999
  still says where coverage begins.
- **An empty table is a 200 with `first_date: null`**, logged as a warning. A misspelt `source`
  gets the same answer.
- **For RK-4:** treat a null `first_date` as "no risk-free data", never as rf = 0.

### RK-4. portfolio: client reads the risk-free series

**Status:** done

Add `get_risk_free_series` to the data-store client Protocol, the HTTP client and the fakes.

Tests: parsing, a negative rate, and a missing key raising `DataStoreApiError` (as in LB-2).

**As built:** `get_risk_free_series(start, end)` → `RiskFreeSeries(first_date, points)`, each
point a `RiskFreePoint(date, rate, series)` with a `Decimal` rate, unfilled. Decisions:
- **Nothing stored is `None`** (agreed), as `get_fx_rate`'s no-rate is: data-store's
  `first_date: null` can't reach a caller as rf = 0, and RK-8/RK-11 grey the rf figures
  rather than fail. Points with a null `first_date` are a `DataStoreApiError`.
- **`risk/risk_free.py` keeps its float `RatePoint`**, so `risk/` still imports nothing from
  `datastore_client`. The risk flows convert.
- **No fake gains the method yet** (agreed). Each is a per-file duck type and nothing calls it,
  so RK-8 and RK-11 add it to the fakes their flow tests use.

### RK-5. portfolio: shared EUR daily returns

**Status:** done

A preparatory refactor. Extract a pure function that turns an asset's `PricePoint`s and
`FxPoint`s into a daily EUR return series: r_eur = (1 + r_local)·fx₍t−1₎/fxₜ − 1 (§4.2), with
FX taken on or before each date. **NULLs are kept as NULLs.** The result is "ineligible: no FX
series for <CCY>" when there is no FX. Labs' `_Series` then builds its levels on this function,
and bridges NULLs itself.

The result also exposes its two components, `r_local` and `r_fx`, beside `r_eur`. RK-16's
local-currency correlation and RK-26's FX decomposition need them, and recomputing them
elsewhere would make a second copy of the conversion.

Tests:
- §4.2's worked example, including its `r_local` and `r_fx`.
- An EUR asset.
- The series starting at the later of the first return and the first FX date.
- **An equality test**: a no-gap fixture's chained EUR returns equal Labs' growth exactly, and
  every existing Labs test passes unchanged.

**As built:** `portfolio/eur_returns/calculator.py`, `eur_daily_returns(prices, fx)` →
`EurReturns(anchor, dates, r_local, r_fx, r_eur, fx)` or `Ineligible(reason)`. It is Decimal, like
Labs; RK-6's float library converts at its boundary. The ineligible reason is a code
(`NO_HISTORY`, `NO_FX_SERIES`), not a message: the caller words "no FX series for <CCY>" from
`TickerInfo.fx_currency`, and Labs keeps its "no FX rate". Labs chains `r_local` into its levels
and prices them with the module's `FxSeries`, rather than chaining `r_eur`, because it prices on
the union calendar, where an FX move on a day the asset didn't trade has no `r_eur` of its own.
The equality test (USD and EUR) pins that the two forms agree on the asset's own dates, at Labs'
8 places.

### RK-6. portfolio: risk library foundations

**Status:** done

- A new pure module, `portfolio/risk/`. It does no I/O and works on floats.
- **Add numpy to portfolio.** *Recommendation (confidence: medium–high):* §6.3 and §7 are matrix
  maths, and §5.5 needs a documented quantile method. numpy gives both, tested, and handles
  20-year daily series cheaply. Risk figures are statistical estimates, not ledger amounts, so
  floats are right here. `Decimal` stays for money. The alternatives were pure Python
  (`statistics` + hand-written matrices), which means more code to get wrong, and pandas, which
  isn't needed for index-aligned arrays. Confirm before adding the dependency.
- **Types:** a return series (dates, values with NULLs, frequency). A window. A result,
  `RiskFigure(value, window_start, window_end, frequency, q, n_obs, n_gaps, flags)`, with the
  flags §9 lists.
- **Building blocks:**
  - weekly W-FRI aggregation with NULL propagation (R6)
  - gap accounting and the 2% warning (R8)
  - `has_suspect_rows`
  - the empirical q (§4.4)
  - ACT/360 risk-free compounding with forward-fill (§4.1), and R4's `rf_partial`
  - the minimum-history check (§4.5, R9)
  - standard windows ending at the latest common date (§4.5)
  - the VaR day mask: days on which every included asset's exchange traded (R7, §5.5)

Tests: a week with a NULL becomes NULL. A week with no trades is NULL. A holiday Friday keeps
its label. rf compounding over a weekend. Empirical q for a union calendar. Each minimum's
boundary. The VaR mask drops a day on which one asset's exchange was closed.

**As built:** numpy is added to portfolio (agreed). `portfolio/risk/` has `types`, `weekly`,
`quality`, `history`, `windows`, `risk_free` and `var_mask`, and no caller yet. Decisions:
- **Weekly edges** (agreed): partial weeks at either end are dropped, so every week runs
  Friday to Friday. `to_weekly` takes an `as_of` date, so a last week ending on a holiday Friday
  counts once that Friday has passed.
- **VaR mask** (agreed): an asset traded on a day if data-store has a price row for it.
  data-store keeps rows for some zero-volume holidays, so one can pass the mask. The fix is
  exposing `volume` on the price endpoint: RK-31.
- `has_suspect_rows` is any non-NULL |r| > 100%. data-store nulls every other such row, so this
  catches exactly the ones it kept.
- The daily empirical q counts NULL days as days of the calendar (`n_obs + n_gaps`).
- R8's budget is a new flag, `data_gaps`. `RiskFigure` also has a `reason`, for R9's text.
- `risk_free` uses a local `RatePoint` until RK-4 adds the client type. It compounds exactly,
  not by §4.1's simple approximation. A day after the series begins with no rate on or before it
  is an error, never a rate filled forward from nothing.

### RK-7. portfolio: single-series measures

**Status:** done

§5.1–5.5, each function frequency-agnostic (q is a parameter). A thin layer applies §4.3's
frequency rule, so callers can't feed a daily series to a weekly measure by accident:
- cumulative return and CAGR (not annualised under 1Y), daily
- volatility and downside deviation (divisor N), weekly
- drawdown: MDD, peak/trough/recovery dates, max time under water, and the DD series, daily
- Sharpe ± SE (Lo 2002), Sortino, weekly; Calmar (`short_window` under 3Y) from daily CAGR and MDD
- 1-day historical VaR/CVaR 95/99, with linear interpolation, documented; on RK-6's day mask,
  with `nonsync_daily` for multi-market series and `tail_unstable` below 1,000 obs (§4.5)

Tests: **§5.7's golden test** (at its pinned q = 252). A recovered and an unrecovered drawdown.
VaR on a known ordered sample. Every insufficient-history path, including the weekly minimum of
52. The `tail_unstable` boundary.

**As built:** the maths is in `risk/measures.py`, `drawdown.py` and `tail.py`, frequency-agnostic
with q as a parameter. The layer callers use is `risk/single_series.py`: `return_figures`,
`drawdown_figures`, `weekly_figures`, `calmar_figure` and `tail_figures`. Each takes an unsliced
series and a `Window`, and raises on the wrong frequency. §5.7's golden test runs against the maths,
the only place daily data at q = 252 can reach a Sharpe. portfolio's `architecture.md` moved its
Testing section to `docs/testing.md` first, to stay under 64 KB. Decisions:
- **Weekly windows shrink to whole weeks** (Friday on or after the start, to Friday on or before
  the end), and the figure reports the shrunk window. RK-6 drops partial weeks, so otherwise a
  daily window rarely fits the weekly series. Risk-free returns run Friday to Friday.
- **VaR's quantile is numpy's `linear`** (Hyndman–Fan type 7). CVaR averages the returns at or
  below it.
- **An undefined ratio** (no variation, no return below rf, no drawdown) is greyed with a reason,
  never inf or 0.
- **The Sharpe's SE** is a new `RiskFigure.standard_error`, set on Sharpe only.
- **`nonsync_daily` is a caller argument.** The library doesn't know a series' markets.
- **A greyed drawdown keeps its DD series**, with None on gap dates, so RK-8's chart works on
  periods under a year. Max time under water is the longest episode, which needn't be the deepest.

### RK-8. portfolio: Labs risk in the backtest

**Status:** done

Add a `risk` section to `POST /labs/backtest`, for the mix and each asset:
- §5.1–5.5's measures per window
- the drawdown series for the requested growth period
- CAGR

The mix uses Labs' buy-and-hold series, **drift included** (R3), re-based at each window's
start, as Labs' periods are.

Rules to agree in this task:
- **Windows.** The spec has 1Y/3Y/5Y/10Y/Max, and Labs' periods are 1M–5Y/Max. Recommended: the
  risk table uses the spec's windows, so the calculator also builds 3Y and 10Y, and periods
  under 1Y never get risk figures.
- **Gaps.** For the risk path, the mix's daily return on a day when any included asset's return
  is NULL is itself a gap, while the levels stay bridged as Labs builds them.
- **Frequencies.** Volatility, Sharpe and Sortino come from the mix's weekly returns (spec
  §4.3), so the drifting mix needs a weekly series as well as a daily one. VaR uses RK-6's day
  mask and carries `nonsync_daily` when the mix spans markets or currencies.

The flow also fetches the risk-free series. Extending the backtest route, rather than adding a
new one, keeps one fetch and one set of statuses. Measure the response size against LB-5's
figure.

Tests: hand-computed fixtures. The drifting mix differs from constant weights. A NULL day is
excluded from the mix's stats but still in its chart. Per-asset rows under R10's common-window
rule.

**As built:** a `risk` section on `POST /labs/backtest`, computed by a new pure
`labs/risk.py` (`compute_backtest_risk`) and fetched for by `labs/flows.py`. It is null exactly
when no asset is included. Decisions:
- **The spec's windows** (recommended above): 1Y/3Y/5Y/10Y/Max. Labs periods under 1Y never get
  risk figures.
- **One common window for every row** (agreed). Max starts on the youngest asset's first day,
  and `limited_by` names it. A window starting earlier is uncovered on every row, the mix's and
  each asset's, as "limited by X (history from <date>)", with no figures. So a 10-year-old asset
  shows no 10Y beside a 2-year-old one, and every covered window holds every asset. The risk
  path therefore needs neither LB-9's re-weighting nor Max's joins.
- **The drawdown series is the mix's only** (agreed), over the requested growth period.
- **The mix drifts from each window's start** (R3), on the union calendar. It is computed from
  the calculator's own per-asset EUR levels, which `_Series` exposes as the now-public
  `AssetSeries`, weighted in numpy floats. A test pins its 1Y and 5Y returns to Labs' Decimal
  ones to 1e-8. Any asset's NULL makes that day a gap (R5), while the levels stay bridged.
  Asset rows use their own `r_eur` (stream 1).
- **The drawdown chart follows Labs' bridged growth line**, with None on gap dates. Measuring
  it on gap-excluded returns, as the MDD figure is, would make it drift off the growth line
  after each gap, because the other assets still moved that day.
- **No risk-free data greys, never rf = 0.** `single_series.weekly_figures_without_rf` (new,
  for RK-11 too) keeps volatility and greys downside deviation, Sharpe and Sortino with a reason:
  "no risk-free rate data" (none stored), "couldn't reach data-store" (the call raised, logged as
  an error), or "no risk-free rate on or before <date>" (data-store's rates stopped before the
  common start, where `risk_free_returns` would otherwise raise). `risk_free.available` says
  which.
- **The risk-free call follows the price fetch**, over ten days before the common start to the
  end, because only the prices give that span. It is skipped when the histories don't overlap.
- **`nonsync_daily` is set by currency**: on a row that includes a non-EUR asset. `TickerInfo`
  has no exchange.
- **Figures are JSON numbers**, not the backtest's fixed-point strings, and each figure's
  flags are sorted.
- **Size and time, synthetic worst case** (every asset on 16.8 years of weekdays, half in USD,
  Max). For 12 assets, the response is 1.1 MB, of which risk is 298 KB, and the compute is
  0.60 s (0.23 s without risk). For 50 assets, it is 3.8 MB, of which risk is 836 KB, and 2.4 s
  (1.1 s without risk). About 155 KB of the risk section is the drawdown series. Real mixes are
  shorter: LB-5 measured 460 KB for the whole 50-asset response. Not yet measured against the
  dev data-store.
- portfolio's `architecture.md` was split first: its module table is now `docs/modules.md`.

### RK-9. web: Labs risk panel and drawdown chart

**Status:** done

- The client parses `risk`.
- A risk table: CAGR, volatility, MDD, Sharpe ± SE (labelled "1 s.e."), Sortino and Calmar per
  window, formatted as §10 says, greyed with its reason where insufficient. Each figure's hover
  names its frequency (weekly or daily).
- A drawdown chart under the growth chart. It reuses `chart_theme.js` and `.chart-frame`.
  **Agree its styling before building.** A drawdown isn't a loss figure, so whether oxblood
  applies is a brand question.
- Glossary entries for every measure, linked with `term`, saying "historical".
- Labelled "buy & hold (drifting)" (R3).

**As built:** `web/labs_risk_view.py` (pure) builds the panel, and `static/labs_drawdown_chart.js`
draws the chart. The client parses `risk` as a required key, as it does `left_out`. web's
`docs/ui-architecture.md` was split first: its Labs entries are now `docs/labs-ui.md`, which
holds RK-9's reasoning. Decisions:
- **Drawdown styling** (agreed): an ink (`--text`) line with a soft `--text-muted` fill down
  from 0%, with no oxblood. A drawdown is a distance below a high, not a loss figure. The max
  drawdown in the tables is neutral mono too, and gold stays rejected (2.2:1 on parchment).
- **Two tables** (agreed): the mix's grid of measures by window, plus the mix and every asset
  over one window, picked by `?risk_window=` links (default 1Y). The links rerun the page, so it
  works without JS.
- **§10's metadata is shown, not hovered**, since `title` tooltips were already rejected for
  the glossary. The window's start is on its column, each measure's frequency on its name, the
  daily and weekly `n_obs` in a "Returns used" row, and a greyed figure's reason in its cell.
- **Flags are plain-word notes** under the figure. An unknown flag shows its code and is logged.
- **Formats:** percentages to one decimal, ratios to two with no "+", and Sharpe as
  "0.86 ± 0.59". Only CAGR is gain/loss colored.
- **Only RK-9's six measures are shown.** Downside deviation, VaR and CVaR are parsed for
  `/risk`.
- **For RK-14:** the drawdown chart's period follows the growth chart's links. A benchmark
  drawdown line, if wanted, would join this chart under the growth chart's palette constraints.

### RK-10. portfolio: daily TWR of invested positions

**Status:** done

Build spec stream 3, computed on read, on the union calendar.

**Definition (agreed 2026-10-20): holdings-based.**
rₜ = Σᵢ wᵢ,ₜ₋₁ · r_eur,i,t, where:
- wᵢ,ₜ₋₁ is ticker i's share of the EUR market value at the previous close, from
  `return_snapshots.market_value_eur`, summed across accounts by ticker.
- r_eur,i,t is RK-5's daily EUR total return, with the dividend on its ex-date.

**Why holdings-based.** A flow-based TWR, (MVₜ + Dₜ − Fₜ) / MV₍t−1₎ − 1, would need each
`DIVIDEND` transaction moved from its pay date to its ex-date (spec §6.1), by matching it to
data-store's dividend events. That is a matching step, with a fallback for unmatched rows, that
can go wrong. The holdings-based form has no flows to time and no dividends to match, and it
shares RK-5's return definition with ex-ante and Labs.

**What it gives up, stated in the docs and in the figure's hover text:**
- dividends count gross of withholding tax
- fees and the gap between trade price and close are ignored
- a position's first day counts from the next close, and its last day runs close to close

These are small next to the risk figures' own sampling error. The cost-basis return on
`GET /returns/portfolio` remains the figure for what was actually earned.

Rules:
- **Buys and sells don't move the return.** They only change the next day's weights.
- **An empty portfolio** (total market value 0 at t−1) has no return that day. The chain
  restarts at the next day with holdings.
- **A NULL r_eur for a held ticker** makes that day a gap (R5), as for any mix.
- **An ineligible holding** (no FX series) is named, not dropped silently. Its weight is
  excluded and the remaining weights are renormalised, with `n_excluded` and the tickers in the
  result.
- It is exposed as a `twr_growth` field on `GET /returns/portfolio`'s points, or as its own
  endpoint, whichever keeps the existing EUR chart's payload unchanged.
- No schema change.

Tests: hand-computed fixtures. A buy mid-series doesn't move the return. A sell. A dividend
whose ex-date and pay date differ: the gain lands on the ex-date, and the pay date shows no
swing. A full exit and re-entry. Two accounts holding one ticker. An FX move on a USD holding. A
NULL day. An ineligible holding.

**As built:** a new package, `portfolio/twr/`: `calculator.py` (pure, Decimal) and `flows.py`.
It is its own route, `GET /returns/portfolio/twr?user_id=`, so `GET /returns/portfolio`'s
payload is unchanged and that route still doesn't call data-store. Decisions:
- **The fetch is shared** (agreed). Labs' concurrent ticker-info, price and FX fetch moved to
  `eur_returns/fetch.py` in a preparatory commit, with Labs' tests unchanged. It takes a start
  per ticker, so the TWR fetches each ticker from its first row. RK-8, RK-11 and RK-17 can use
  it too.
- **Dates with nothing held are left out** (agreed), not marked as gaps. After a full exit, the
  series resumes on the first date after re-entry, and R8's budget isn't spent on time out of
  the market. A week with no dates at all becomes a NULL weekly return in RK-11.
- **Returns between two calendar dates are compounded** into the later one, so a ticker's
  return dated where no snapshot row is (snapshots older than data-store's prices) still counts.
- **A closed pair counts only through its last row.** A sale dated on a day with no price row
  leaves that pair's last return row with quantity above 0. A forward-fill would hold its weight
  for good. Closed pairs come from the latest `position_snapshots`.
- The response carries `n_excluded` and `excluded` (ticker, status, reason). The statuses are
  Labs' own, and the reason for a missing FX series is "no FX series for <CCY>".
- portfolio's `architecture.md` was split first: its Testing section is now `docs/testing.md`.

### RK-11. portfolio: ex-post risk endpoint

**Status:** done

`GET /risk/ex-post?user_id=` returns the §5.1–5.5 measures from RK-10's series over the
standard windows, labelled "ex-post · invested assets". It has its own `portfolio/risk/flows.py`
and jobs entry. It logs a start line, each window's outcome and a summary.

**As built:** `risk/ex_post.py` (pure) and `risk/flows.py`, the only I/O module in `risk/`.
`GET /risk/ex-post?user_id=` →
`{label, base_date, end_date, rf_available, windows: [...], n_excluded, excluded}`. Each window
has `label, start, end, covered`, the twelve figures (each with its window, frequency, q,
`n_obs`, `n_gaps`, flags, reason, standard error) and the max drawdown's dates. portfolio's
`architecture.md` was split first: "Cross-cutting decisions" is now
`docs/cross-cutting-decisions.md`. Decisions:
- **VaR mask per date** (agreed): a date counts when every ticker weighted at the previous date
  has its own return dated on it. RK-10's calculator gained a `TwrDay` per point for this, and
  `PortfolioTwr` each included ticker's FX currency. `GET /returns/portfolio/twr` is unchanged.
- **`nonsync_daily`** is set unless the window held exactly one EUR ticker.
- **No risk-free data** (agreed), whether none is stored or the fetch fails: downside deviation,
  Sharpe and Sortino are greyed, "no risk-free rate data", and the rest are still measured.
  This uses RK-8's `weekly_figures_without_rf`, Labs' wording too. The fetch starts two weeks
  before the base date, so the first week has a rate to forward-fill from.
- **An uncovered window is greyed, not shortened**: "insufficient history (portfolio history
  from <date>)" on every figure, `covered: false`. Labs (RK-8) instead greys an uncovered window
  as a whole, with a window-level `reason` and null figures.
- **No DD series** in the response (agreed). RK-12 shows the summary only.
- **For RK-12:** values are fractions, VaR and CVaR positive losses. An empty portfolio is a 200
  with `windows: []`.

### RK-12. web: `/risk` page with the ex-post summary

**Status:** done

- A new router `web/api/risk.py`, a "Risk" nav link, `templates/risk.html`, and a view builder.
- First section: the ex-post summary, with every figure's window, `n_obs` and label on hover
  (§10).
- Failures are isolated per section, as `/labs` does.
- If `ui-architecture.md` would cross 64 KB, split it first.

**As built:** `GET /risk` (`api/risk.py`), with a loader per section, and so far one section.
The client gained `ex_post_risk`, reusing RK-9's `RiskFigure`, `RISK_FIGURE_KEYS` and figure
parser, and `risk_view.py` builds the table with RK-9's `pct1`, `signed_pct1` and `ratio`.
`ui-architecture.md` wasn't split: RK-9 moved Labs to `labs-ui.md`, leaving it at 49.7 KB.
Decisions (agreed):
- **Measures down, windows across.** Each column header shows its window's dates and each row
  its frequency. A cell's `title` carries its own window, n_obs, gaps and the label. On touch
  screens the title doesn't show, a known cost.
- **Only cumulative return and CAGR are coloured** as gains or losses, signed to 1 decimal as
  RK-9 shows CAGR. Every other figure, max drawdown included, is ink.
- **Flags are numbered markers**, in `risk_view.FLAG_NOTES`' fixed order, with a legend of only
  those present. `insufficient_history` gets none, because the greyed cell already says it.
  RK-9 words flags as short notes under each figure instead, so the two pages keep separate
  wordings.
- **Glossary entries** for the measures RK-9 didn't define: `ex-post`, cumulative return,
  downside deviation, VaR and CVaR. The rest are RK-9's.
- **An uncovered window says why once**, in its header, and each of its cells says "Not
  covered". A covered window's greyed figure shows its own reason.
- **No risk-free rate** is a warning banner, since the banner has no "info" kind.
- **Against the dev data**, `GET /risk/ex-post` took 0.46 s for a 2021–2026 portfolio.

### RK-13. portfolio: benchmark and relative measures

**Status:** done

- Library: weekly beta, correlation, tracking error and information ratio (§5.6).
- A `benchmark` parameter on the backtest and ex-post endpoints, defaulting to VWCE.DE. One
  benchmark serves every window. A window it doesn't cover is greyed, "benchmark history too
  short (from <date>)", with no fallback to another index (spec §5.6). Each figure names the
  benchmark and its start date.
- **Agree** whether to offer a longer All-World history: track VWRL (same FTSE index,
  ~2012, not tracked today), or a VWCE-on-IWDA splice exposed as its own, named benchmark. Either
  is opt-in, never a silent default.
- The backtest also returns the benchmark's growth line for the chart period.

Tests: beta/correlation on a constructed series with a known answer. VWCE.DE on a 10Y window
greyed with its start date, not swapped for IWDA.AS. An unknown benchmark ticker reported, not
fatal.

**As built:** `risk/relative.py` (the four measures), on the overlap `risk/pairing.py` builds
(extracted from `correlation.py`'s pairwise cell first, so both use the same weeks). A new
package, `benchmark/`, fetches the benchmark (`flows.py`) and turns it into return series
(`base.py`), for both endpoints. `POST /labs/backtest` takes `"benchmark"` and
`GET /risk/ex-post` takes `?benchmark=`, both defaulting to `VWCE.DE`. portfolio's `flows.md`
was split first: the risk and Labs flows are now `docs/risk-flows.md`. Decisions:
- **A longer All-World history: VWRL** (agreed), opt-in, no splice. The CHECK found `VWRL.AS`,
  EUR, from 2012-05-22, distributing, so its total return compares with VWCE's. Tracking it is
  a data-store onboarding step with no code.
- **Tracking error and the information ratio need 52 weeks** (agreed), like volatility and
  Sharpe. Beta and correlation keep §4.5's 104. The spec's §4.5 table has an "Amended (RK-13)"
  note. A 1Y window (52 or 53 whole weeks) therefore never has a beta.
- **Labs measures the mix and each asset** (agreed), each from its own weekly series. The
  benchmark isn't a row, so it doesn't move the common window.
- **The benchmark's growth line is null when it starts after the growth period** (agreed), with
  `growth_reason`. It is priced as an asset's line is (`labs/calculator.growth_line`), so it
  matches the mix's.
- **The benchmark is fetched over its whole history**, so the first date a greyed window names is
  its own. It is fetched separately even when it's also in a Labs mix: two more calls, and one
  code path.
- **An unavailable benchmark** (not tracked, no history, no FX series, or a failed call) greys
  every relative figure with "benchmark unavailable: <reason>", and the response still answers
  200. The response names the benchmark: `{ticker, available, reason, first_date}`.
- **Undefined figures are greyed**: a constant benchmark greys beta and correlation, a constant
  series greys correlation (its beta is a real 0), and identical series show a tracking error of
  0 and grey the information ratio.
- **For RK-14:** the relative figures are each window's `relative`, and the line is
  `risk.benchmark.growth`, one value per `growth.dates` entry, as fixed-point strings like the
  other lines. Ex-post's `relative` is never null: an uncovered window's figures are greyed like
  its others. Labs' is null on an uncovered window, like its other figures.

### RK-14. web: benchmark selector, relative measures, benchmark line

**Status:** done

- A benchmark field (a datalist of tickers) on `/labs` and `/risk`, kept in the query string.
- Relative-measure rows.
- The benchmark line on the Labs growth chart, with **its style agreed** (LB-8's palette
  constraints apply).

**As built:** the client sends `benchmark` on both calls and parses RK-13's `relative`,
benchmark status and growth line as required keys. `benchmark_param.py` is the field's rule, and
`partials/_benchmark_field.html` is its form on both pages. web's `labs-ui.md` and
`ui-architecture.md` hold the reasoning. Decisions:
- **The line is dashed ink, on by default** (agreed): a 1.5px `--text` line, dashed, under the
  mix and over the assets, named by its ticker at its last point, with its own checkbox, checked.
  With no line, the box is disabled and a note gives portfolio's `growth_reason`.
- **Labs: rows in the grid and columns in the comparison** (agreed). The comparison names the
  benchmark once above the table, because naming it under four headers pushed the table past
  laptop width.
- **Labs: its own form in the risk section** (agreed), re-sending the rest of the query as
  hidden inputs. Every other link and form keeps `benchmark`.
- **Only an over-long ticker is caught in web** (over 20 characters, portfolio's limit, which
  would be a 422). It falls back to `VWCE.DE` with an inline error. An untracked ticker is sent,
  and portfolio's "benchmark unavailable: not tracked" is shown, said once in the group heading,
  with each cell reading "Unavailable".
- **The benchmark and its first date head the relative group** (spec §5.6). On `/risk`, each
  cell's `title` also ends "· against <ticker>".
- **No benchmark drawdown line:** portfolio returns no series for one.
- **Sharp edge:** a string property of our own on a Chart.js dataset froze a toggled chart
  mid-animation, so `labs_chart.js` derives each line's series instead (`labs-ui.md`).

### RK-15. portfolio: ex-ante covariance and risk decomposition

**Status:** done

- Library (§6.2–6.3): the listwise weekly window (R12), with the limiting holding named, and Σ
  annualised ×52.
- σ_p, MCR, CR, PCR, correlation with the portfolio, the diversification ratio and the effective
  N.
- The constant-weight series of "today's mix": daily for drawdown and VaR (a NULL in any holding
  excludes that day), weekly for volatility, Sharpe and Sortino (spec §6.2).

Tests: **§6.3's golden test**. CR sums to σ_p. PCR sums to 100%. A negative-PCR hedge. The
listwise window shortened by one late holding. The constant-weight weekly series' stdev × √52
equals σ_p on the same window.

**As built:** the maths is `risk/covariance.py`, where §6.3's golden test runs. The layer callers
use is `risk/ex_ante.py`: `decomposition_figures(weekly, weights, window)` and
`constant_weight_series(series, weights)`. R12's panel is `risk/listwise.py`'s
`ListwiseReturns`. Decisions:
- **A late holding shortens the window** (agreed), as R12 says, rather than greying it. The
  figures report the shortened window, and `limited_by` names the holding(s) that set its start.
- **σ_p greys below 52 weeks, the decomposition below 104** (agreed). σ_p equals the
  constant-weight weekly volatility exactly, so it takes volatility's minimum. MCR, CR, PCR,
  correlation with the portfolio and the diversification ratio rest on correlations, so they
  take covariance's. The effective N never greys.
- **R13 is held by type.** `ex_ante.py` takes a `ListwiseReturns`, never a bare matrix. For
  RK-16: build its maths path on `listwise.py` and keep the pairwise matrix a different type.
- **The constant-weight series is a series, not new measures.** Callers pass it to RK-7's
  `single_series` layer. On its daily union calendar, a holding with no row is a closed
  exchange at 0% (R7, which `var_days` masks out of VaR), and any NULL makes the day a gap.
- **Weights are validated, not normalised.** Each must be above 0, they must sum to 1, and
  they must name the same holdings as the returns. Otherwise the call raises. RK-17 normalises.
- **A holding with identical returns has exactly zero covariance.** As in `measures.sharpe`,
  float rounding would otherwise leave about 1e-18.

### RK-16. portfolio: correlation matrix

**Status:** done

Pearson on weekly returns:
- **Pairwise for display**: each cell carries its own `n_obs` and start date.
- **Listwise for maths** (R13). The type system keeps a pairwise matrix out of any portfolio
  calculation.
- EUR or local-currency returns.
- Clustering order by √(2(1−ρ)) (optional within this task).

Tests: pairwise cells with different overlaps. EUR raises two USD assets' correlation over local
in a constructed FX fixture.

**As built:** `risk/correlation.py`. Decisions:
- **The listwise ρ is built on RK-15's `ListwiseReturns`** (`risk/listwise.py`), as RK-15's note
  asks. RK-16 was planned to own R12's panel, with RK-15 building on it. RK-15 merged first, so
  RK-16 dropped its own panel when merging main. R12 has one implementation, and the listwise ρ
  and Σ always come from the same weeks.
- **Pairwise is kept out of maths by construction and at runtime** (agreed; portfolio runs no
  type checker). `PairwiseCorrelation` exposes only `cell(a, b)` (`RiskFigure`s), never an array,
  and `listwise_correlation` raises `TypeError` on anything but a `ListwiseReturns`.
- **A pairwise cell** covers the weeks that pair shares inside the window, so a series needn't
  cover the window. Its start is the base of its first shared week. The diagonal is computed like
  any cell.
- **A constant series is greyed**, "no variation in returns" (the listwise reason names it),
  never inf or 0.
- **Currency is the caller's**: the same functions take weekly series from `r_eur` or `r_local`.
  RK-17 picks and labels.
- **No clustering order** (agreed). It's left to RK-18, its first consumer.
- `quality.any_suspect` checks loose values, so the threshold stays in one place.

### RK-17. portfolio: ex-ante endpoint

**Status:** done

`POST /risk/ex-ante` takes `{assets, window, benchmark, exclude?}`. It returns σ_p, the
decomposition table, the constant-weight measures, and the correlation matrix over the holdings
plus the benchmark. It names the holding that limits the window. Per-asset statuses work as in
LB-4 (`no_fx_rate`, `no_history`, …). The € tail figures (§5.5) are left to web, which knows the
market value.

**As built:** `POST /risk/ex-ante` →
`{label, currency, rf_available, window, benchmark, statuses, sigma_p, diversification_ratio,
effective_n, holdings, constant_weight, correlation}`. A new pure `risk/ex_ante_report.py` puts
the parts on their windows, and `risk/flows.compute_ex_ante` fetches. Two preparatory refactors
came first. The TWR's fetch-and-word loop is now `eur_returns/eligibility.py`, shared with
ex-ante and, for its wording, the benchmark. Ex-post's figure code is now
`ex_post.series_figures`, which measures the constant-weight mix. portfolio's `risk-flows.md`
has the details. Decisions:
- **`window` is 1Y/3Y/5Y/10Y/Max, default 3Y** (agreed; §7.1's default). It ends on the
  holdings' latest common date. Max starts on the oldest holding's first day, so a younger
  holding shortens it and `limited_by` names it. 1Y never has a decomposition (104 weeks).
- **One requested window, three uses.** The decomposition runs on its listwise panel (R12). The
  constant-weight figures run over the panel's (shortened) window, so their weekly volatility is
  σ_p exactly, which a test pins. The pairwise matrix keeps the requested window, each cell over
  its own overlap (R13). The response gives both starts: `requested_start` and `start`.
- **Constant-weight figures: all twelve of ex-post's**, plus the max drawdown's dates, in
  ex-post's shape (agreed). There are no relative figures.
- **`currency: eur | local`** (agreed), default EUR. It changes only the matrix. σ_p and the
  decomposition are always EUR.
- **`exclude`** lists tickers from the mix. They get status `excluded` ("excluded by request")
  and aren't fetched, and the rest are renormalised. A ticker not in the mix, or excluding
  everything, is a 422. The other statuses are the TWR's wording ("no FX series for <CCY>").
- **The matrix** is the holdings in request order, then the benchmark. A benchmark held as a
  holding isn't repeated, and `benchmark_key` names its row. An unavailable one has no row.
  `cells` is the upper triangle with the diagonal.
- **The risk-free fetch starts two weeks before the oldest holding's first day**, so one call
  covers whichever window the panel settles on.
- **Nothing included** is a 200 with only `statuses` and `benchmark`, as in Labs.
- **Size and time, synthetic worst case** (16.8 years of weekdays, half USD, Max): 12 assets
  give 25 KB in 0.18 s, and 50 assets give 294 KB in 1.1 s, almost all of it the 1,326 cells.
  Not yet measured against the dev data-store.
- **For RK-18:** values are fractions. `holdings` figures are null exactly when
  `diversification_ratio` is greyed. Mirror `cells` for the heatmap. For RK-19: the request
  already takes any mix, so a candidate's "after" is the same shape.

### RK-18. web: ex-ante summary, decomposition and heatmap

**Status:** done

- Ex-ante summary, labelled "ex-ante · constant weights" beside the ex-post one (R2).
- The decomposition table, with weight % beside risk %, and the 10%-weight/25%-risk insight
  surfaced.
- The VaR € figure ("on a bad day (1 in 20)…").
- The correlation heatmap. **Agree its colour scale against the brand charter before building.**
  Forest and oxblood are off-limits for correlation.
- A control to exclude the holding that limits the window.

**As built:** a second `/risk` section, built by a new pure `risk_ex_ante_view.py` and loaded by
`api/risk.py` from `GET /holdings` (`labs_view.build_default_mix`) then `POST /risk/ex-ante`.
The client gained `ex_ante_risk`. `risk_view.py`'s cell, title and legend helpers became public
first, so both sections' figures read alike. web's `ui-architecture.md` holds the reasoning.
Decisions (agreed):
- **The heatmap is an ink ramp on ρ from 0 to 1**: tints of `--text` at 10/20/30/80%, ρ below
  0.2 (negative included) unfilled, the sign in the printed number. AA allows ink text up to a
  33% tint and flipped text from 69%, so only the darkest step (ρ ≥ 0.8) flips. No gold,
  forest or oxblood.
- **The insight note** names each holding whose share of risk is ≥ 1.5× its weight and ≥ 5
  points above it, and any hedge. Every row shows weight, share of risk and their ratio.
- **The € VaR** applies the constant-weight 95% and 99% figures to the market value of the
  holdings portfolio included, not the whole portfolio's.
- **Controls:** window links (`?ex_ante_window=`, 3Y by default) and `?exclude=` links per
  limiting holding, with "include again" links. **No EUR/local toggle**: the matrix is EUR.
- **σ_p is the summary's volatility row**, since it equals the constant-weight weekly
  volatility; the diversification ratio is under it and the effective N under the table.
- A stale or all-covering `?exclude=` is dropped in web and logged, never sent as a 422.
- `review_pages.py`'s contrast check now parses `color(srgb …)`, which Chromium reports for
  `color-mix()`. The script is at 61.7 KB: the next task adding review pages should split it
  first.

### RK-19. portfolio: candidate what-if

**Status:** done

- Extend the ex-ante endpoint, or add a sibling, with `candidate {ticker, weight}` and
  `funding: pro_rata | replace:<ticker>`. "From cash" is deferred.
- Before and after on the same listwise window. If adding the candidate shortens the window, it
  says so and recomputes "before" (§7.2).
- The table in §7.2, with the risk deltas (Δσ_p, Δ diversification ratio, the candidate's % of
  risk) first and ΔSharpe second, with its SE.
- The threshold SR_c > ρ(c,p)·SR_p, with its inputs, **under pro-rata funding only**. All three
  come from weekly returns on the listwise window (spec §7.2).
- R14's survivorship flag, when the candidate isn't in `tracked_tickers`. **CHECK** which field
  of `TickerInfo` tells that.

Tests: pro-rata and replace weights. The shortened window. The threshold on a constructed case,
checked against a numerical derivative of the mix's Sharpe. No threshold under "replace". The
flag.

**As built:** `POST /risk/what-if`, a sibling of ex-ante (agreed): `risk/what_if.py`,
`risk/flows.compute_what_if`. Reasoning in portfolio's `risk-flows.md`. Decisions:
- **"Before" holds the candidate at weight 0** (agreed): one listwise panel for both mixes. A
  0 weight is now allowed (RK-15 said > 0). Replace is capped at X's weight (`capped`).
- **R14 CHECK:** `TickerInfo.status` is None for a ticker priced only through a fund.
- **Nothing to compare is a 200** (agreed); the reason is in `candidate.status` or
  `funding.reason`.
- **For RK-20:** show `candidate.weight` when `capped`, R14's warning on `survivorship`, and
  `threshold.reason` under replace.

### RK-20. web: what-if form

**Status:** done

A GET form on `/risk` (ticker, weight %, funding), working without JS as LB-7's does. It renders
the before/after table, the threshold explanation and the survivorship warning.

**As built:** a `/risk` section after ex-ante, built by a new pure `risk_what_if_view.py` and
loaded by `api/risk.py` as a third loader on the holdings call that ex-ante and look-through
share. The client gained `what_if`. `ui-architecture.md`'s `/risk` notes moved verbatim to a new
`risk-ui.md` first (58 KB), which holds the reasoning. Decisions:
- **Funding is one select** (agreed): pro-rata, then "Replace X" per holding left in.
- **The window and exclusions are ex-ante's** (agreed), resolved once for both, so "before" is
  the mix that section measures.
- **The ticker is checked for length only** (agreed); the tracked list is the datalist. The
  form catches only what portfolio would 422, and a form with errors makes no call. A "replace
  X" gone stale (X excluded since) is an inline error, not a switch to pro-rata.
- **Weight is a % of the mix after the change**, portfolio's meaning, sent as a fraction.
  `labs_mix.parse_weight` (made public) reads it.
- **Changes are never coloured**: percentages in points (new `signed_points1` filter), ratios
  signed (`signed_ratio`).
- **RK-19's three asks:** a capped replace names the weight the candidate got; R14's warning is
  a banner in the spec's words, with markers on every "after" figure; under replace the
  threshold line gives `threshold.reason`. `FLAG_NOTES["survivorship"]` was reworded for
  RK-19's meaning, since only the what-if sets it.
- **The before/after weights aren't parsed or shown.**
- **Not done here:** the dev-stack check RK-19 quoted (EGLN.L, MSFT, NVCR). The review fakes
  mirror those cases.

### RK-21. data-store: ETF holdings endpoint

**Status:** done

`GET /etf-holdings/{fund_key}/latest` returns the snapshot date and constituents (ticker, name,
sector, asset class, location, currency, weight %). **CHECK** how a fund_key maps to the
tradeable ticker (IWDA.AS), and expose that mapping.

**As built:** the CHECK found no mapping. `FundConfig` had no ticker, the deployed `isin` is null,
and `IWDA.AS` is an unrelated `tracked_tickers` row. Decisions (agreed):
- **The link is config.** `etfs.yaml` funds gain an optional `ticker`, so there is no schema
  change. A ticker claimed by two funds is a `ConfigError`. `GET /etf-holdings` lists every
  configured fund (enabled or not) with `ticker` and `latest_as_of_date`, so RK-22 finds the
  fund for a holding there.
- **Every line is served**, including cash, FX, futures and money market (about 0.5% of IWDA),
  each with its `asset_class`, heaviest first. Weights are as stored, not normalised: IWDA's
  2026-08-30 snapshot sums to 100.03.
- **Each line carries `yfinance_ticker`**: what ingest prices it as, resolved with
  `ticker_resolution.py`. It is null for a line that isn't equity, has no symbol, or is on an
  unmapped exchange. RK-22 matches a directly held stock to a constituent on it, not on the raw
  `ticker`.
- **404 for a `fund_key` outside `etfs.yaml`, and for a configured fund with no snapshot**, with
  distinct messages. A disabled fund still serves its last snapshot.
- **For RK-22:** add `ticker: IWDA.AS` to the deployed `etfs.yaml`. `as_of_date` is the scrape
  date, not iShares' own. `currency` is the listing currency (iShares' `GBP` for London lines).
  The client method goes on the Protocol, the HTTP client and the fakes, as in RK-4.

### RK-22. portfolio: look-through exposures

**Status:** done

§8 by currency, sector, country and asset class:
- a direct stock looked up in the latest snapshot, otherwise "unknown"
- IWDA through its holdings
- other ETFs in an "unmapped: <ETF>" bucket
- top-N weights, Herfindahl and the effective number per dimension
- the snapshot date
- R16: constituent currency vs listing currency, labelled "listing currency of holdings"
- single-name overlap: look-through aggregated by issuer, so a stock held directly and through
  IWDA is one exposure
- currency-hedged ETFs as "hedged: <ETF>" in the currency view. **CHECK** whether any holding is
  hedged, and agree how that's known.

**ETCs:** `asset_type` is wrong for EGLN.L and ISLN.L (Discovery). Agree where commodity and
underlying currency come from (e.g. a small config mapping) before building. Don't trust
`asset_type`.

Tests: hand-computed look-through. The unmapped share. A world ETF in EUR whose exposure is
mostly USD. A stock held directly and through IWDA, summed. A hedged ETF kept out of the
constituent currencies.

**As built:** `POST /risk/look-through` takes `{assets: [{ticker, weight}]}`, as ex-ante does,
and returns each asset's kind and status, the snapshots, a coverage split, the five dimensions'
buckets, and the top 25 names, each with its concentration. A new package, `look_through/`:
`calculator.py` (pure, Decimal), `issuer.py`, `instruments.py` and `flows.py`. The client gained
`list_etf_funds` and `get_latest_etf_holdings`. The deployed `etfs.yaml` already had
`ticker: IWDA.AS`. No schema change. portfolio's `risk-flows.md` holds the reasoning.
Decisions:
- **The hedged CHECK:** no holding is hedged (checked on the dev data, 2026-10-22). How one is
  known (agreed): a hand-kept `HEDGED` table in `instruments.py`, empty for now. A ticker in it
  goes into the currency view as "hedged: <ticker>", and its other dimensions still look
  through.
- **ETCs come from the same table** (agreed), never from `asset_type`: EGLN.L, ISLN.L and
  **SGLD.MI**, which Discovery missed (data-store tags it `etc` correctly). Each is "Commodity:
  <gold|silver>" in USD, with country "n/a (commodity)". Two ETCs on one commodity are one name.
- **An issuer is (name without share-class suffix, location)** (agreed). On IWDA's snapshot it
  merges exactly the six real multi-line issuers (Alphabet, Lindt, …) and keeps the US and
  Swedish "EQT" apart. A false merge needs one name in one country for two companies. None
  occurs today; it's documented as the sharp edge.
- **Herfindahl and effective N cover the known share only** (agreed), rescaled, with
  `known_share` beside them. Each bucket has a kind: `known`, `unknown`, `unmapped` or `other`.
- **Classification:** an ETC, then a fund with a snapshot (looked through), then a tracked
  `etf`/`etc` (unmapped, with a `note` saying why), else a direct stock. A direct stock matches
  a fund line on data-store's `yfinance_ticker`. Unmatched, its sector and country are
  "unknown", but its currency is its own listing currency and its asset class "Equity".
- **Snapshots are rescaled to 100%** (IWDA sums to 100.03), with the raw sum returned. Only
  equity lines are names. Cash, FX, futures and money-market lines stay in the other dimensions.
- **Two currency views** (R16): `currency`, labelled "listing currency of holdings", and
  `trading_currency`, each held ticker's own.
- **Failures:** a ticker-info failure leaves that asset out (the rest renormalised), and a
  snapshot failure shows that fund unmapped. A failed fund list or tracked-ticker list is a 502,
  because an ETF would otherwise be read as a stock.
- **Against the dev data** (11 holdings): 0.17 s and 10 KB. 70.7% looked through, 13.1%
  commodity, 12.8% unmapped (VWCE, IEMA, IUSN) and 3.4% direct. Constituent currency is 67.8%
  USD, against 5.6% by trading currency. BRK-B and PEP merge with their IWDA lines, and gold is
  the largest single name at 11.0%.
- **For RK-23:** weights are fixed-point string fractions. `exposures` is null when nothing is
  included. Show `coverage.unmapped` prominently, and use each dimension's `label`.

### RK-23. web: concentration section

**Status:** done

Exposure tables or bars per dimension, with the unmapped share shown prominently, the
listing-vs-exposure currency view, the top look-through single names, and the snapshot date.

**As built:** a third `/risk` section, built by `risk_concentration_view.py`. The client
gained `look_through`, and `review_pages.py`'s fakes moved to `scripts/review_fakes.py` first.
web's `ui-architecture.md` holds the reasoning. Agreed:
- A table per dimension, each weight with an inline-SVG ink bar (the CSP blocks inline styles).
- The unmapped share first, with each fund's reason.
- One currency table with both R16 views.
- The 10 heaviest known buckets, with the rest in a `<details>`.
- Always the whole mix: `?exclude=` stays ex-ante's.
- One holdings call shared with ex-ante.

### RK-24. portfolio: rolling windows

**Status:** done

- Rolling 1Y volatility and Sharpe, stepped monthly (ex-post and ex-ante).
- Rolling 52-week correlation, stepped weekly, for a pair or for an asset against the portfolio.
- A window below its minimum yields a gap, not a point.

**As built:** a new pure `risk/rolling.py`, served on the existing endpoints. `GET /risk/ex-post`
and `POST /risk/ex-ante` each gain `rolling`, and the ex-ante request takes an optional
`rolling_correlation: {a, b}`. `jobs.py` (68.5 KB) was split first: the risk read jobs are now
`risk_jobs.py`. portfolio's `risk-flows.md` ("Rolling windows") holds the reasoning. Decisions:
- **The rolling correlation takes 52 weeks with a minimum of 52** (agreed), §7.1 as written.
  Under §4.5's 104 every point would be a gap. The spec's §4.5 has an "Amended (RK-24)" note.
  *Rejected: a 104-week window*, which hides the crisis spikes the view is for.
- **The existing endpoints are extended** (agreed), as RK-8 extended the backtest, so there is one
  fetch per section and nothing new is fetched.
- **"The portfolio" is the ex-ante constant-weight weekly mix** (agreed), with the asset in it,
  as `corr_with_portfolio` has it. `b: null` asks for it, and the response names it
  `"portfolio"`.
- **Monthly windows end on each month's last series date** and start a calendar year earlier.
  They begin at the first full year, and the last one is the 1Y window, so its point equals the
  1Y figure (tested for both).
- **Ex-ante rolling covers the mix's whole history**, whatever `window` asks for.
- **Rolling correlation is always EUR**, whatever `currency` says. A pair ticker in neither the
  mix nor the benchmark, a ticker `exclude` leaves out, or a == b is a 422. A ticker that is
  left out or an unavailable benchmark gives empty `points` and a `reason`, never an error.
- **Every point is a full `RiskFigure`**, and a gap is greyed in place. **Sharp edge:** one NULL
  week can turn a 1Y point into a gap (a year is 52 or 53 weeks, and the minimum is 52), as on
  the standard 1Y window.
- **Size and time, synthetic worst case** (RK-17's: 16.8 years, half USD, Max, with a pair):
  `rolling` is 77 KB (191 points), and the pair 161 KB (823 points). The ex-ante response is 267
  KB in 0.31 s for 12 assets (25 KB before RK-24) and 564 KB in 1.3 s for 50. Not measured
  against the dev data-store: the dev `portfolio-api` image predates the change.
- **For RK-25:** values are fractions, and each point is a figure. Plot it at `window_end`, and
  break the line at `value: null` (show its `reason`). Rolling ρ carries an SE of about 0.14,
  and its axis is ρ, not a gain or a loss, so no forest or oxblood. Web picks the default pair
  (it sends none today).

### RK-25. web: rolling charts

**Status:** done

Charts for RK-24, on `/risk`.

**As built:** `risk_rolling_view.py` (pure), `partials/_rolling_charts.html` and
`static/risk_rolling_chart.js`. The client parses both rolling keys as required and sends
`rolling_pair`. First, web's `ui-architecture.md` was split: Risk is now `docs/risk-ui.md`, which
holds the reasoning. Agreed:
- Each section draws its own charts: ex-post's under its table, ex-ante's after the heatmap,
  with the pair.
- Volatility and Sharpe are two stacked ink charts, Sharpe with a ± 1 s.e. band. ρ's axis is
  fixed at −1..1.
- The default pair is the heaviest holding against the portfolio (`?corr_a=&corr_b=`). A pair
  portfolio would 422 falls back to it with a note.
- Gaps break the line, and each run of them gets a note under the chart.
- Not measured against the dev data, because its `portfolio-api` image predates RK-24.

### RK-26. FX decomposition (portfolio and web)

**Status:** done

§6.4 for each non-EUR holding, on weekly returns: local volatility, FX volatility,
corr(r_local, r_fx), and EUR volatility. It reuses RK-5's `r_local` and `r_fx`.

**As built:** a new pure `risk/fx_decomposition.py`, served as `fx_decomposition` on
`POST /risk/ex-ante`, and a table under the decomposition on `/risk`. Reasoning is in
portfolio's `risk-flows.md` and web's `risk-ui.md`. The spec's §6.4 has an "Amended (RK-26)"
note. Decisions (agreed):
- **The rows run on the decomposition's listwise panel** (R12), which `ExAnteDecomposition`
  now carries. So a row's EUR volatility is the holding's volatility in the decomposition
  table (tested), and a late holding shortens both. *Rejected: each holding over its own
  weeks*, as a heatmap cell is, which would show two volatilities for one holding.
- **Served on the ex-ante endpoint**, as RK-24 served `rolling`: nothing more is fetched.
- **Weekly components compound exactly:** (1 + l)(1 + f) = 1 + e per week, as per day.
- **Minimums:** 52 weeks for the volatilities, 104 for the correlation, so 1Y greys the
  correlation. Between 52 and 104 weeks the EUR volatility shows while the decomposition's is
  null. A pegged currency greys the correlation.
- **EUR holdings and the benchmark get no row.** An all-EUR mix gives `[]`, and web says so.
- **web shows the four figures and a fixed note, in ink.** *Rejected: a derived "currency
  share of risk"*, which doesn't add up because of the correlation and cross terms.

### RK-27. data-store: regimes endpoint

**Status:** done

`GET /regimes?start&end` returns the rows that overlap the range.

**As built:** `GET /regimes?start&end` → `{total_regimes, regimes: [...]}`. Each regime carries
every column but `ingested_at`, ordered by `start_date, regime_id`. The read is
`regimes/repository.py`'s `get_overlapping_regimes` and `count_regimes`, through `read_flows.py`'s
`regimes_overlapping`. There's no schema change. The reasoning is in data-store's `read-api.md`.
Decisions (agreed):
- **Only the range.** There are no `dimension` or `scope` filters: the table holds about a
  hundred rows, so the caller filters.
- **Overlap is inclusive at both ends**, and a NULL `end_date` is ongoing. **Dates are
  unclipped.**
- **`total_regimes` is the whole table's count**, so 0 ("nothing loaded", logged as a warning)
  reads differently from an empty range, as RK-3's `first_date: null` does.
- **`known_from` and `notes` are served.** `known_from` isn't applied (§11).
- **For RK-28:** add `get_regimes` to the client Protocol, the HTTP client and the fakes, as in
  RK-4. Treat `total_regimes: 0` as "no regimes loaded", not as "none in this window". Clip each
  regime to the window, and filter by dimension or scope there.

### RK-28. Regime view (portfolio and web)

**Status:** done

For each regime overlapping the window, the portfolio's and the benchmark's return and MDD inside
it. This is descriptive only, so `known_from` isn't applied (§11 phase 5).

**As built:** `get_regimes` on portfolio's data-store client. A pure `risk/regimes.py` clips
each regime to the series and measures it with `single_series.regime_figures`. Both endpoints
serve the result as `regimes`. On web, `risk_regime_view.py` and `partials/_regime_table.html`
render a "Market regimes" block in each section. The reasoning is in portfolio's
`risk-flows.md` and web's `risk-ui.md`. The spec's §4.5 and §11 have "Amended (RK-28)" notes.
Decisions (agreed):
- **Both series, each labelled (R2).** Ex-post runs on the TWR. Ex-ante runs on the
  constant-weight daily mix (`ExAnteReport.mix_daily`, added for this) over its whole history,
  whatever the window, as rolling does. In dev, ex-post reaches back only to 2021, while today's
  mix reaches its holdings' common start.
- **Every overlapping regime is shown, grouped by dimension** in web. There is no filter, and
  portfolio serves them all.
- **No §4.5 minimum beyond one return** (`Measure.REGIME`). The drawdown is flagged with a new
  `short_regime` under 200 obs instead of being greyed. *Rejected: 200*, which greys nearly
  every regime, and *a 5-day minimum*. No CAGR.
- **Clip and flag.** A regime is a `Window` from its start date to its end date, so a drawdown
  regime that starts on its peak close measures peak-to-trough. A regime the series covers only
  in part is measured on the overlap and marked `partial`, with its measured dates. One with no
  end runs to the last date and is marked `ongoing`. One with no return inside is left out. The
  benchmark is measured on the same clipped window, or greyed with §5.6's wording.
- **The fetch fails alone.** Only `DataStoreApiError` is caught. A failure gives "regimes
  unavailable: data-store call failed", and `total_regimes: 0` gives "no regimes loaded" with a
  warning. Both answer 200.
- **Each dimension is a group closed by default** (agreed after the first build), whose summary
  names it and counts its regimes: ex-ante's Max can list 60+ regimes with real data.
- **Not measured against the dev stack:** the dev `portfolio-api` and `web` images predate the
  change.

### RK-29. Candidate ranking mode

**Status:** done

Score many candidates at a fixed weight on a common window, showing each candidate's history
length, with R14's warning once at the top. Rank by Δσ_p and Δ diversification ratio. ΔSharpe
is shown with its SE but is never the ranking key, because picking the best of many noisy
historical Sharpe deltas is data-mining (spec §7.2). Agree the candidate universe and a cap on it
before building, since every candidate is a data-store fetch.

**As built:** `POST /risk/what-if/ranking` (`risk/ranking.py`, `risk/flows.compute_ranking`) and
a fifth `/risk` section (`risk_ranking_view.py`). portfolio's `risk-flows.md` and web's
`risk-ui.md` hold the reasoning. Decisions (agreed):
- **The universe is the tracked tickers**: enabled, `ready`, outside the mix (excluded
  holdings included). R14 never applies. **The cap is 50**, alphabetically, and the ones past
  it are named, not fetched.
- **The window is the holdings'.** A candidate that doesn't cover it is listed unranked with
  R9's reason in weeks rather than shortening it for the rest. The spec's R10 has an RK-29
  amendment recording this.
- **Order: Δσ_p ascending by default, Δ diversification ratio descending by link.** ΔSharpe ±
  s.e. is shown in both and is never an order. A row missing either key is listed greyed,
  unnumbered.
- **Each row is the what-if's for that candidate** under pro-rata funding, on its own listwise
  panel (a test pins it), measuring only the decomposition and weekly Sharpes. 1Y is a 422, and
  web sends nothing on 1Y. The ranking runs only when its form is sent.
- **Preparatory splits:** web's client parsers moved to `portfolio_client/risk_parsing.py` and
  `parsing.py` (`http_client.py` was 57 KB). The review fake is in `scripts/review_ranking.py`
  (`review_fakes.py` is 59 KB).
- **Not done here:** the dev-stack check (timing at the real universe size, one row against
  the what-if form). Synthetic worst case: 0.5 s at 3Y, 0.8 s at Max for 50 candidates and 12
  holdings, before the fetches.
