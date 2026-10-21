# Tasks

Roadmap for the risk chunk: measure how risky a portfolio is and whether the risk was worth it,
how its holdings move together, what adding a candidate would change, and where it is
concentrated. The domain rules — formulas, windows, minimums, gap handling, labels — are in
[`docs/domain/risk.md`](docs/domain/risk.md) (the spec), and this file cites them by section and
rule number (§5.4, R12). It doesn't restate them. The other open thread is
[`tasks-labs-thread.md`](tasks-labs-thread.md). Its Deferred items "Annualized return (CAGR)",
"A benchmark line" and "Risk figures: volatility and maximum drawdown" are delivered here
(RK-9, RK-14).

**Task IDs are prefixed `RK-`** so they can't be confused with the walking-skeleton's bare
"task N" references or the other chunks' `UI-N`, `TK-N`, `PF-N`, `LY-N` and `LB-N`.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency.

## Discovery (spec Phase 0)

The spec's Phase 0 was done read-only, against the code and the dev DB, on 2026-10-17.
Findings, and what they change:

- **There is no portfolio TWR.** `GET /returns/portfolio` (`returns/performance.py`) sums EUR
  market value per date. Its percentage is the total return on cost basis. Nothing tracks cash:
  DEPOSIT and WITHDRAWAL are recorded but never summed. Spec stream 3 is therefore built in
  RK-10, as a TWR of **invested positions only**. Ex-post figures are labelled "invested assets
  risk" (spec §6.1). The calendar is the union of the holdings' price dates, which the empirical
  q in §4.4 handles.
- **Per-ticker TWR (stream 3b) doesn't exist either.** `return_snapshots` covers the holding
  period, on cost basis. "Risk while I held it" is deferred.
- **EUR conversion** is Labs' `_Series.growth` (`labs/calculator.py`). It is private, and its
  input is built by `_build_series`, which also bridges NULLs as flat days. RK-5 extracts the
  conversion so that Labs and the risk path share it (spec §4.2 CHECK). The conversion stays in
  portfolio, not data-store: §9's "data-store owns EUR conversion" is superseded, because
  portfolio already owns it and Labs must give identical numbers.
- **The benchmark tickers:** `VWCE.DE` is EUR-listed, from 2019-07-29. `IWDA.AS` is EUR-listed,
  from 2009-09-25.
- **FX comes from yfinance** (`fx_rates`, source `yfinance`). There are no HKD, JPY, CAD, DKK,
  AUD or SEK series, so assets in those currencies are ineligible ("no FX series for <CCY>").
- **Asset types are unreliable for ETCs.** `tracked_tickers` stores EGLN.L (gold) and ISLN.L
  (silver) as `equity`. This matters to RK-22.
- **No settings table** exists in any service. The benchmark is a request parameter (below).

## Domain review (2026-10-20)

A finance review of the spec. The golden tests (§5.7, §6.3) and §4.2's example were recomputed
independently, and every value matches. The spec's §13 lists the rules it revised. The facts it
checked against the code, docs and dev DB:

- **`market_returns.simple_return` is a total return**, with the dividend on its ex-date
  (`(close_t + dividends_t) / close_(t−1) − 1`).
- **Portfolio's market value is price-only**, and `DIVIDEND` transactions are dated on
  payment (`position_snapshots.dividends_received_eur` sums by `trade_datetime`). This matters
  to RK-10.
- **NULL returns are suppressed implausible rows**, usually a pair around a garbage close on a
  zero-volume day. The real move across them is lost, not just missing (spec R5).
- **No VWRL** (or other long-history All-World ticker) is tracked. Only `VWCE.DE` (from
  2019-07-29) and `IWDA.AS` (from 2009-09-25) are. This matters to RK-13.

## Agreed requirements

- **Benchmark.** It is chosen per request, as a query parameter, and is not saved. The default
  is `VWCE.DE`. One benchmark serves every window: a window it doesn't cover is greyed, with no
  fallback to `IWDA.AS` (spec §5.6, D2; changed by the domain review, because the two track
  different indices). Saving a choice is deferred, because it needs a schema.
- **Frequencies** (spec §4.3, changed by the domain review): volatility, downside deviation,
  Sharpe and Sortino are weekly, like correlation and beta. Cumulative return, CAGR, drawdown
  and VaR stay daily.
- **Ex-post means invested assets only.** Cash isn't tracked. Spec §6.2's cash toggle and §7.2's
  "from cash" funding rule are deferred until it is.
- **Ex-ante takes a mix, as Labs does.** Risk endpoints take `{assets: [{ticker, weight}]}`. web
  builds the default mix from holdings with `labs_view.build_default_mix`. This keeps weights
  computed in one place, `build_portfolio_holdings`, and lets the what-if and Labs reuse the same
  shape.
- **The rest of the spec's decisions D1–D10 stand** unless a task below changes one and says so.

## Constraints that apply to every task

- **Schema changes are agreed first.** RK-2 adds a table. Propose its columns, types and keys,
  and get sign-off before writing DDL (`.claude/rules/data-schema.md`). Update `docs/schema.md`
  in the same change. No other task changes a schema. If one turns out to need it, stop.
- **web talks only to portfolio.** Risk-free, benchmark and holdings data reach web through
  portfolio endpoints.
- **Returns come from `simple_return`, never from close prices.**
- **A NULL return is a gap (R5).** In the risk path it is excluded, never 0. The Labs growth
  chart keeps its flat-day bridging (D6).
- **Spec golden tests are hand-checked, not regenerated.** §5.7 and §6.3 are pinned values. If
  the code disagrees, find out which side is wrong before changing either.
- **Every figure carries its metadata.** That is its window, frequency, q, `n_obs`, `n_gaps`,
  flags, and ex-post/ex-ante label (§9, §10). Insufficient history is greyed with its reason
  (R9), never shown as 0 or left blank.
- **Copy says "historical", never "expected" (R15).**
- **Brand.** Use tokens only. Every number is in `--font-mono`. Gold is the only accent. Forest
  and oxblood are used only on gains and losses: a volatility or a correlation is not a gain or
  a loss.
- **Tests first** (`.claude/rules/testing.md`). Integration tests run only against
  `docker-compose.test.yml`.
- **Logging** follows `.claude/rules/logging.md`: a start line, an outcome for each asset
  (including why one was left out), and a summary. data-store also follows its own
  `logging.md`.
- **Docs change in the same change as the code.** That means each touched service's
  `docs/architecture.md`, plus portfolio `flows.md` and web `ui-architecture.md`.
- **Files stay under 64 KB.** If a doc would cross the limit, split it first, in a commit of its
  own (as LB-4 and LB-6 did).
- **Visual review.** Every web task adds `review_pages.py` entries. It regenerates the
  screenshots and looks at them at every width, in both themes.

## Status table

| ID | Task | Depends on | Status |
|----|------|------------|--------|
| RK-0 | [Roadmap housekeeping](#rk-0-roadmap-housekeeping) | — | done |
| RK-1 | [data-store: split `architecture.md`](#rk-1-data-store-split-architecturemd) | — | done |
| RK-2 | [data-store: risk-free rate ingestion (€STR + EONIA)](#rk-2-data-store-risk-free-rate-ingestion-str--eonia) | RK-1 | done |
| RK-3 | [data-store: risk-free endpoint](#rk-3-data-store-risk-free-endpoint) | RK-2 | done |
| RK-4 | [portfolio: client reads the risk-free series](#rk-4-portfolio-client-reads-the-risk-free-series) | RK-3 | done |
| RK-5 | [portfolio: shared EUR daily returns](#rk-5-portfolio-shared-eur-daily-returns) | — | done |
| RK-6 | [portfolio: risk library foundations](#rk-6-portfolio-risk-library-foundations) | — | done |
| RK-7 | [portfolio: single-series measures](#rk-7-portfolio-single-series-measures) | RK-6 | done |
| RK-8 | [portfolio: Labs risk in the backtest](#rk-8-portfolio-labs-risk-in-the-backtest) | RK-4, RK-5, RK-7 | done |
| RK-9 | [web: Labs risk panel and drawdown chart](#rk-9-web-labs-risk-panel-and-drawdown-chart) | RK-8 | ready |
| RK-10 | [portfolio: daily TWR of invested positions](#rk-10-portfolio-daily-twr-of-invested-positions) | RK-5 | done |
| RK-11 | [portfolio: ex-post risk endpoint](#rk-11-portfolio-ex-post-risk-endpoint) | RK-4, RK-7, RK-10 | done |
| RK-12 | [web: `/risk` page with the ex-post summary](#rk-12-web-risk-page-with-the-ex-post-summary) | RK-11 | ready |
| RK-13 | [portfolio: benchmark and relative measures](#rk-13-portfolio-benchmark-and-relative-measures) | RK-8, RK-11 | ready |
| RK-14 | [web: benchmark selector, relative measures, benchmark line](#rk-14-web-benchmark-selector-relative-measures-benchmark-line) | RK-9, RK-12, RK-13 | blocked |
| RK-15 | [portfolio: ex-ante covariance and risk decomposition](#rk-15-portfolio-ex-ante-covariance-and-risk-decomposition) | RK-6, RK-7 | done |
| RK-16 | [portfolio: correlation matrix](#rk-16-portfolio-correlation-matrix) | RK-6 | done |
| RK-17 | [portfolio: ex-ante endpoint](#rk-17-portfolio-ex-ante-endpoint) | RK-4, RK-5, RK-13, RK-15, RK-16 | blocked |
| RK-18 | [web: ex-ante summary, decomposition and heatmap](#rk-18-web-ex-ante-summary-decomposition-and-heatmap) | RK-12, RK-17 | blocked |
| RK-19 | [portfolio: candidate what-if](#rk-19-portfolio-candidate-what-if) | RK-17 | blocked |
| RK-20 | [web: what-if form](#rk-20-web-what-if-form) | RK-18, RK-19 | blocked |
| RK-21 | [data-store: ETF holdings endpoint](#rk-21-data-store-etf-holdings-endpoint) | RK-1 | done |
| RK-22 | [portfolio: look-through exposures](#rk-22-portfolio-look-through-exposures) | RK-21 | ready |
| RK-23 | [web: concentration section](#rk-23-web-concentration-section) | RK-12, RK-22 | blocked |
| RK-24 | [portfolio: rolling windows](#rk-24-portfolio-rolling-windows) | RK-11, RK-16, RK-17 | blocked |
| RK-25 | [web: rolling charts](#rk-25-web-rolling-charts) | RK-18, RK-24 | blocked |
| RK-26 | [FX decomposition (portfolio and web)](#rk-26-fx-decomposition-portfolio-and-web) | RK-18 | blocked |
| RK-27 | [data-store: regimes endpoint](#rk-27-data-store-regimes-endpoint) | RK-1 | ready |
| RK-28 | [Regime view (portfolio and web)](#rk-28-regime-view-portfolio-and-web) | RK-14, RK-27 | blocked |
| RK-29 | [Candidate ranking mode](#rk-29-candidate-ranking-mode) | RK-20 | blocked |
| RK-30 | [Ledoit–Wolf shrinkage (optional)](#rk-30-ledoitwolf-shrinkage-optional) | RK-15 | ready |
| RK-31 | [VaR day mask on traded volume](#rk-31-var-day-mask-on-traded-volume) | RK-1 | ready |

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

**Status:** blocked

- The client parses `risk`.
- A risk table: CAGR, volatility, MDD, Sharpe ± SE (labelled "1 s.e."), Sortino and Calmar per
  window, formatted as §10 says, greyed with its reason where insufficient. Each figure's hover
  names its frequency (weekly or daily).
- A drawdown chart under the growth chart. It reuses `chart_theme.js` and `.chart-frame`.
  **Agree its styling before building.** A drawdown isn't a loss figure, so whether oxblood
  applies is a brand question.
- Glossary entries for every measure, linked with `term`, saying "historical".
- Labelled "buy & hold (drifting)" (R3).

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

**Status:** ready

- A new router `web/api/risk.py`, a "Risk" nav link, `templates/risk.html`, and a view builder.
- First section: the ex-post summary, with every figure's window, `n_obs` and label on hover
  (§10).
- Failures are isolated per section, as `/labs` does.
- If `ui-architecture.md` would cross 64 KB, split it first.

### RK-13. portfolio: benchmark and relative measures

**Status:** ready

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

### RK-14. web: benchmark selector, relative measures, benchmark line

**Status:** blocked

- A benchmark field (a datalist of tickers) on `/labs` and `/risk`, kept in the query string.
- Relative-measure rows.
- The benchmark line on the Labs growth chart, with **its style agreed** (LB-8's palette
  constraints apply).

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

**Status:** blocked

`POST /risk/ex-ante` takes `{assets, window, benchmark, exclude?}`. It returns σ_p, the
decomposition table, the constant-weight measures, and the correlation matrix over the holdings
plus the benchmark. It names the holding that limits the window. Per-asset statuses work as in
LB-4 (`no_fx_rate`, `no_history`, …). The € tail figures (§5.5) are left to web, which knows the
market value.

### RK-18. web: ex-ante summary, decomposition and heatmap

**Status:** blocked

- Ex-ante summary, labelled "ex-ante · constant weights" beside the ex-post one (R2).
- The decomposition table, with weight % beside risk %, and the 10%-weight/25%-risk insight
  surfaced.
- The VaR € figure ("on a bad day (1 in 20)…").
- The correlation heatmap. **Agree its colour scale against the brand charter before building.**
  Forest and oxblood are off-limits for correlation.
- A control to exclude the holding that limits the window.

### RK-19. portfolio: candidate what-if

**Status:** blocked

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

### RK-20. web: what-if form

**Status:** blocked

A GET form on `/risk` (ticker, weight %, funding), working without JS as LB-7's does. It renders
the before/after table, the threshold explanation and the survivorship warning.

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

**Status:** blocked

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

### RK-23. web: concentration section

**Status:** blocked

Exposure tables or bars per dimension, with the unmapped share shown prominently, the
listing-vs-exposure currency view, the top look-through single names, and the snapshot date.

### RK-24. portfolio: rolling windows

**Status:** blocked

- Rolling 1Y volatility and Sharpe, stepped monthly (ex-post and ex-ante).
- Rolling 52-week correlation, stepped weekly, for a pair or for an asset against the portfolio.
- A window below its minimum yields a gap, not a point.

### RK-25. web: rolling charts

**Status:** blocked

Charts for RK-24, on `/risk`.

### RK-26. FX decomposition (portfolio and web)

**Status:** blocked

§6.4 for each non-EUR holding, on weekly returns: local volatility, FX volatility,
corr(r_local, r_fx), and EUR volatility. It reuses RK-5's `r_local` and `r_fx`.

### RK-27. data-store: regimes endpoint

**Status:** ready

`GET /regimes?start&end` returns the rows that overlap the range.

### RK-28. Regime view (portfolio and web)

**Status:** blocked

For each regime overlapping the window, the portfolio's and the benchmark's return and MDD inside
it. This is descriptive only, so `known_from` isn't applied (§11 phase 5).

### RK-29. Candidate ranking mode

**Status:** blocked

Score many candidates at a fixed weight on a common window, showing each candidate's history
length, with R14's warning once at the top. Rank by Δσ_p and Δ diversification ratio. ΔSharpe
is shown with its SE but is never the ranking key, because picking the best of many noisy
historical Sharpe deltas is data-mining (spec §7.2). Agree the candidate universe and a cap on it
before building, since every candidate is a data-store fetch.

### RK-30. Ledoit–Wolf shrinkage (optional)

**Status:** ready

Shrink Σ when the assets exceed about ¼ of the weekly observations. It is flagged in the result.
Build it only if RK-18's matrices show the need.

### RK-31. VaR day mask on traded volume

**Status:** ready

Surfaced by RK-6. The VaR day mask (R7, §5.5) counts a day as traded when data-store has a
price row for it. data-store keeps rows for some zero-volume holidays, such as UK bank
holidays, so such a day passes the mask, and its near-zero return crowds the distribution's
centre.
- **data-store:** the price endpoint adds each row's `volume`, joining `raw_market_data`
  (`market_returns` doesn't carry it). A NULL volume stays NULL, as data-store's suppression
  rule treats it: it is not zero. No schema change. The endpoint change is documented in
  data-store's `docs/read-api.md` (RK-1).
- **portfolio:** `PricePoint` gains `volume`, and the HTTP client and fakes parse it. A missing
  key raises `DataStoreApiError`, as in LB-2. The trading days handed to `var_days` are those
  with volume > 0. Decide here whether a NULL volume counts as traded.
- Update `risk/var_mask.py`'s docstring and portfolio's `architecture.md`, both of which name
  this as the known limitation.

Tests: endpoint parsing with and without volume. A zero-volume holiday row left out of the
mask. A NULL-volume day handled as agreed.

## Deferred

- "Risk while I held it" from a per-ticker TWR (spec stream 3b).
- Cash in ex-ante (§6.2's toggle) and the "from cash" funding rule (§7.2). Both need cash
  tracking.
- A saved benchmark choice per user. It needs a settings schema.
- FX series for HKD, JPY, CAD, DKK, AUD and SEK, so those assets become eligible.
- Look-through for VWCE, EM IMI and Small Cap. It needs their holdings ingested.
