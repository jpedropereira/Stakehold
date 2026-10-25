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

## Discovery and domain review

Spec Phase 0's discovery (2026-10-17) and the finance review (2026-10-20) are in
[`docs/domain/risk-findings.md`](docs/domain/risk-findings.md). Tasks below cite them as
"Discovery".

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
  `docs/architecture.md`, plus portfolio `flows.md` and web `ui-architecture.md` (with its
  split-outs `labs-ui.md` and `risk-ui.md`).
- **Files stay under 64 KB.** If a doc would cross the limit, split it first, in a commit of its
  own (as LB-4 and LB-6 did).
- **Visual review.** Every web task adds `review_pages.py` entries. It regenerates the
  screenshots and looks at them at every width, in both themes.

## Status table

A done task's subsection is in [`tasks-risk-thread-done.md`](tasks-risk-thread-done.md),
split out of this file for size; its row here links there.

| ID | Task | Depends on | Status |
|----|------|------------|--------|
| RK-0 | [Roadmap housekeeping](tasks-risk-thread-done.md#rk-0-roadmap-housekeeping) | — | done |
| RK-1 | [data-store: split `architecture.md`](tasks-risk-thread-done.md#rk-1-data-store-split-architecturemd) | — | done |
| RK-2 | [data-store: risk-free rate ingestion (€STR + EONIA)](tasks-risk-thread-done.md#rk-2-data-store-risk-free-rate-ingestion-str--eonia) | RK-1 | done |
| RK-3 | [data-store: risk-free endpoint](tasks-risk-thread-done.md#rk-3-data-store-risk-free-endpoint) | RK-2 | done |
| RK-4 | [portfolio: client reads the risk-free series](tasks-risk-thread-done.md#rk-4-portfolio-client-reads-the-risk-free-series) | RK-3 | done |
| RK-5 | [portfolio: shared EUR daily returns](tasks-risk-thread-done.md#rk-5-portfolio-shared-eur-daily-returns) | — | done |
| RK-6 | [portfolio: risk library foundations](tasks-risk-thread-done.md#rk-6-portfolio-risk-library-foundations) | — | done |
| RK-7 | [portfolio: single-series measures](tasks-risk-thread-done.md#rk-7-portfolio-single-series-measures) | RK-6 | done |
| RK-8 | [portfolio: Labs risk in the backtest](tasks-risk-thread-done.md#rk-8-portfolio-labs-risk-in-the-backtest) | RK-4, RK-5, RK-7 | done |
| RK-9 | [web: Labs risk panel and drawdown chart](tasks-risk-thread-done.md#rk-9-web-labs-risk-panel-and-drawdown-chart) | RK-8 | done |
| RK-10 | [portfolio: daily TWR of invested positions](tasks-risk-thread-done.md#rk-10-portfolio-daily-twr-of-invested-positions) | RK-5 | done |
| RK-11 | [portfolio: ex-post risk endpoint](tasks-risk-thread-done.md#rk-11-portfolio-ex-post-risk-endpoint) | RK-4, RK-7, RK-10 | done |
| RK-12 | [web: `/risk` page with the ex-post summary](tasks-risk-thread-done.md#rk-12-web-risk-page-with-the-ex-post-summary) | RK-11 | done |
| RK-13 | [portfolio: benchmark and relative measures](tasks-risk-thread-done.md#rk-13-portfolio-benchmark-and-relative-measures) | RK-8, RK-11 | done |
| RK-14 | [web: benchmark selector, relative measures, benchmark line](tasks-risk-thread-done.md#rk-14-web-benchmark-selector-relative-measures-benchmark-line) | RK-9, RK-12, RK-13 | done |
| RK-15 | [portfolio: ex-ante covariance and risk decomposition](tasks-risk-thread-done.md#rk-15-portfolio-ex-ante-covariance-and-risk-decomposition) | RK-6, RK-7 | done |
| RK-16 | [portfolio: correlation matrix](tasks-risk-thread-done.md#rk-16-portfolio-correlation-matrix) | RK-6 | done |
| RK-17 | [portfolio: ex-ante endpoint](tasks-risk-thread-done.md#rk-17-portfolio-ex-ante-endpoint) | RK-4, RK-5, RK-13, RK-15, RK-16 | done |
| RK-18 | [web: ex-ante summary, decomposition and heatmap](tasks-risk-thread-done.md#rk-18-web-ex-ante-summary-decomposition-and-heatmap) | RK-12, RK-17 | done |
| RK-19 | [portfolio: candidate what-if](tasks-risk-thread-done.md#rk-19-portfolio-candidate-what-if) | RK-17 | done |
| RK-20 | [web: what-if form](tasks-risk-thread-done.md#rk-20-web-what-if-form) | RK-18, RK-19 | done |
| RK-21 | [data-store: ETF holdings endpoint](tasks-risk-thread-done.md#rk-21-data-store-etf-holdings-endpoint) | RK-1 | done |
| RK-22 | [portfolio: look-through exposures](tasks-risk-thread-done.md#rk-22-portfolio-look-through-exposures) | RK-21 | done |
| RK-23 | [web: concentration section](tasks-risk-thread-done.md#rk-23-web-concentration-section) | RK-12, RK-22 | done |
| RK-24 | [portfolio: rolling windows](tasks-risk-thread-done.md#rk-24-portfolio-rolling-windows) | RK-11, RK-16, RK-17 | done |
| RK-25 | [web: rolling charts](tasks-risk-thread-done.md#rk-25-web-rolling-charts) | RK-18, RK-24 | done |
| RK-26 | [FX decomposition (portfolio and web)](tasks-risk-thread-done.md#rk-26-fx-decomposition-portfolio-and-web) | RK-18 | done |
| RK-27 | [data-store: regimes endpoint](tasks-risk-thread-done.md#rk-27-data-store-regimes-endpoint) | RK-1 | done |
| RK-28 | [Regime view (portfolio and web)](#rk-28-regime-view-portfolio-and-web) | RK-14, RK-27 | ready |
| RK-29 | [Candidate ranking mode](#rk-29-candidate-ranking-mode) | RK-20 | ready |
| RK-30 | [Ledoit–Wolf shrinkage (optional)](#rk-30-ledoitwolf-shrinkage-optional) | RK-15 | ready |
| RK-31 | [VaR day mask on traded volume](#rk-31-var-day-mask-on-traded-volume) | RK-1 | ready |

## Tasks

### RK-28. Regime view (portfolio and web)

**Status:** ready

For each regime overlapping the window, the portfolio's and the benchmark's return and MDD inside
it. This is descriptive only, so `known_from` isn't applied (§11 phase 5).

### RK-29. Candidate ranking mode

**Status:** ready

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
