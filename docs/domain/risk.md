# Domain spec: risk, risk-adjusted return and correlation

*Stakehold · drafted 2026-10-17 · tasks: [`tasks-risk-thread.md`](../../tasks-risk-thread.md)*

This document defines **what** to compute and **which rules** apply. It does not prescribe code
structure beyond a few boundaries (§9). Turn it into tasks using the phased plan in §11. Anything
marked **CHECK** must be verified against the codebase before implementing. Anything marked
**DECISION** is a default the owner can override.

Phase 0 (discovery) found that some premises below were false. Each such place carries an
**Amended (RK-0)** note. The discovery findings are in the task thread's "Discovery" section.

A domain review on 2026-10-20 revised several rules in place: the frequency split, the
benchmark, VaR days, the what-if threshold and a few smaller points. §13 lists each change and
why it was made.

---

## 1. Scope and goals

The owner is a EUR-based investor with ~12 positions (ETFs, ETCs, single stocks) traded in EUR
and USD. The feature must answer:

1. **How risky is my portfolio, and was the risk worth it?** Volatility, drawdown, tail loss and
   risk-adjusted return, both as it actually happened and as currently held.
2. **How do my holdings move together?** A correlation matrix, and how much each holding
   contributes to total risk.
3. **What happens if I add X?** For a candidate ticker at a chosen weight: the change in risk,
   risk-adjusted return and diversification.
4. **Where am I concentrated?** By currency, sector and country, looking through ETFs.

Non-goals for now: forecasting returns, optimisation (efficient frontier), factor models,
implied volatility, intraday risk, bonds and credit.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Return series** | A sequence of periodic simple returns `r_t` at a regular frequency. All risk measures take a return series as input. |
| **Growth index** | `G_t = Π(1 + r_s)` for s ≤ t, with `G_0 = 1`. Drawdown and CAGR use it. |
| **EUR return** | An asset's total return seen by a EUR investor, including the FX move (§4.2). |
| **Window** | `[start, end]` dates plus a frequency. Every reported number carries its window. |
| **Ex-post** | Measured on what actually happened: the realised portfolio TWR series. |
| **Ex-ante (historical)** | Today's weights applied to each asset's historical EUR returns. It answers "how risky is what I hold *now*". It is not a forecast. |
| **Excess return** | `r_t − rf_t`, the return above the risk-free rate for the same period. |
| **Candidate** | A ticker the owner is considering adding. It's evaluated with ex-ante methods only. |

---

## 3. Which return stream supports which measure

| Stream (from existing system) | Usable for risk? | Use it for |
|---|---|---|
| 1. Per-asset daily total return (local ccy) | Yes, **after EUR conversion** (§4.2) | Per-asset stats, correlation, covariance, candidates, ex-ante portfolio |
| 2. Per-position % of cost basis | **No.** It is a since-purchase level, not a periodic series, and depends on entry price. | Nothing in this spec. Never compute volatility or Sharpe from it. |
| 3. Whole-portfolio daily TWR growth | Yes. Derive `r_t = G_t / G_(t-1) − 1`. | Ex-post portfolio risk |
| 3b. Per-ticker TWR growth | Yes, but it only covers the holding period | Optional "risk while I held it". Per-asset stats default to stream 1 in EUR. |
| 4. Labs buy-and-hold backtest | Yes. Use the simulated daily EUR series **including drift**. | Risk of a hypothetical mix as it would have evolved |

> **Amended (RK-0):** streams 3 and 3b don't exist. `GET /returns/portfolio` sums EUR market
> value per date, and its percentage is a return on cost basis. Stream 3 is built in RK-10, as a
> TWR of invested positions only. Stream 3b ("risk while I held it") is deferred.

**Rule R1.** Every risk function takes a return series (dates + values + frequency), never prices,
positions or cost basis.

**Rule R2.** Ex-post and ex-ante numbers are always labelled as such. They answer different
questions and will differ.
- Ex-post includes past trades and past cash levels.
- Ex-ante uses today's mix over the whole window.

**Rule R3.** The Labs backtest's drift is a property of the simulation and is reported as-is. The
ex-ante calculation uses **constant** weights (implicitly rebalanced each period). Label them as
"buy & hold (drifting)" and "constant weights" when shown together.

---

## 4. Inputs and data preparation

### 4.1 Risk-free rate (new data: prerequisite)

There is no risk-free series today. Add one daily EUR series, `rf_eur`, as an annualised rate in %:

| Period | Source | Value used |
|---|---|---|
| 2019-10-01 → today | **€STR** (ECB) | €STR |
| 1999-01-04 → 2019-09-30 | **EONIA** (ECB) | EONIA − 0.085 pp, the ECB's fixed spread linking the two |
| before 1999-01-04 | none | Rate unavailable. See rule R4. |

- Candidate sources: the ECB Data Portal (€STR volume-weighted trimmed mean; EONIA historical
  series). FRED also republishes €STR. **CHECK** the exact series keys at ingestion time.
- Store it in `macro_indicator_rates` or a new table, with source and splice metadata.
- **Day count:** €STR and EONIA are ACT/360. The risk-free return for a period from `d0` to `d1`
  is `rf = Π over calendar days d in [d0, d1) of (1 + rate_d/100/360) − 1`. Each day uses the
  latest published rate on or before it, forward-filling weekends and holidays. The simple
  approximation `rate_(d0)/100 × days/360` is acceptable if documented.
- **R4.** If a window starts before 1999-01-04, Sharpe and Sortino use rf = 0 for the
  uncovered part, and the UI shows a note. This should be rare in practice, because USD assets
  only have EUR returns from 2003 anyway.
- Rates can be negative (2014–2022). Never floor at 0.

### 4.2 EUR conversion of asset returns

`fx_rates` stores `fx_t` = units of currency per 1 EUR. For an asset quoted in currency C:

```
r_fx,t   = fx_(t-1) / fx_t − 1             (return of holding 1 unit of C, measured in EUR)
r_eur,t  = (1 + r_local,t) × (1 + r_fx,t) − 1
```

- Use the same "latest rate on or before date" convention as today.
- An EUR asset has `r_fx = 0`.
- Coverage: USD from 2003, GBP 2000, CHF 2003, PLN 2002. An asset whose currency has no FX series
  is **ineligible**. Show it as "no FX series for <CCY>", never silently drop it.
- The EUR return series starts at the later of the asset's first return and the first FX date.
- **CHECK.** Labs already does this conversion. Reuse that code path, don't write a second one.
  The Labs conversion and this one must produce identical numbers.
  - **Amended (RK-0):** `fx_rates` comes from yfinance, not the ECB. The conversion is Labs'
    private `_Series.growth` (portfolio, `labs/calculator.py`), mixed in with the code that fills
    NULL days as flat. RK-5 extracts it. There are no HKD, JPY, CAD, DKK, AUD or SEK series.

**Worked example.** USD stock +2.0% local, EURUSD moves from 1.10 to 1.12 (the dollar weakened):
`r_fx = 1.10/1.12 − 1 = −1.7857%`, `r_eur = 1.02 × 1.10/1.12 − 1 = +0.1786%`.

### 4.3 Calendars, gaps and frequency

There are two frequencies, each with its own purpose:

| Frequency | Used for | Why |
|---|---|---|
| **Daily** | Cumulative return, CAGR, drawdown, VaR/CVaR | Drawdowns need daily resolution, and VaR is a 1-day measure by definition |
| **Weekly** (Friday close to Friday close) | Volatility, downside deviation, Sharpe, Sortino, correlation, covariance, beta, tracking error, ex-ante portfolio volatility, risk contributions, candidate what-if | US and European closes are ~5.5 h apart, holidays differ, and yfinance FX bars are stamped at neither close. Daily cross-asset covariances are therefore biased toward 0 (non-synchronous trading and carry-forward zeros). Weekly returns remove most of that bias. |

**Why volatility is weekly too, not only correlation.** The variance of a mix is the sum of its
cross-covariances, so the bias that understates daily correlation also understates the daily
volatility of any series that combines markets: a portfolio, a Labs mix, a constant-weight mix,
and even a single USD asset converted to EUR (its local return and its FX return are sampled at
different times). Computing volatility from daily data and σ_p from weekly data would also show
two different volatilities for the same mix. One weekly rule for all second-moment measures
avoids both problems, and costs little: the Sharpe standard error depends on years of history,
not on the number of observations.

**Daily VaR on a multi-market series** carries the same bias, toward smaller losses. It stays
daily because a 1-day loss is what it means, and the UI footnotes it whenever the series mixes
markets or currencies (flag `nonsync_daily`).

Rules:

- **R5. NULL is a gap.** For risk calculations, a NULL daily return is excluded, never treated as
  0. This differs from the current Labs growth-chart behaviour (bridged as a flat day). Leave the
  growth chart as it is (**DECISION**), but the risk path must not inherit the bridging.
  - **A NULL is a lost move, not just a missing observation.** data-store's NULLs come from
    suppressed implausible returns (`market_returns`, "Implausible single-day returns are
    suppressed"): typically the pair of returns into and out of a garbage close on a zero-volume
    day, or a dividend-glitch day. The real move across those days isn't recoverable from
    returns, so excluding them leaves cumulative return, CAGR and max drawdown slightly off.
    It's rare (~460 rows across ~1,300 tickers), and R8 counts it. A bridged return across the
    suppressed run, `close_(t+1) / close_(t−1) − 1`, would recover the move. That's a data-store
    change and out of scope for now.
- **R6. Weekly aggregation.** An asset's weekly return is `Π(1 + r_eur,d) − 1` over the trading
  days in that week (Friday ending, `W-FRI`). If **any** daily return in the week is NULL, the
  weekly value is NULL for that asset. If the asset didn't trade at all that week, the value is
  also NULL. The week is labelled by its Friday date even if Friday was a holiday.
- **R7. Carry-forward days.** When daily series are aligned on the union calendar (existing
  convention), a day on which an asset's exchange was closed is a 0% return. That's acceptable
  for cumulative return, drawdown and the existing growth chart. It is **not** acceptable for
  correlation or volatility, which is why R6 aggregates weekly. It isn't acceptable for VaR
  either, because the zeros crowd the distribution's centre and shrink the tail quantile. So the
  VaR path keeps only days on which every included asset's exchange traded (§5.5).
- **R8. Gap budget.** Each computed stat records `n_obs`, `n_gaps` (NULLs excluded) and the
  window. If gaps exceed 2% of expected observations, show a data-quality warning.
- The ~17 deliberately kept >100% moves stay in. Tail measures (VaR/CVaR, max drawdown) can be
  sensitive to them, so record whether such a row falls inside the window (`has_suspect_rows`)
  and show a footnote.

### 4.4 Annualisation

- `periods_per_year (q)`: 52 for weekly. For daily, the **empirical** value
  `n_obs / (calendar_days_spanned / 365.25)`. This keeps different calendars (union calendar,
  US-only, EU-only) on a comparable footing. Under §4.3 no reported figure annualises daily
  data, so the daily q is the library's own mode, used by its tests and by anything a later
  revision chooses to annualise daily.
- Library functions take `q` as an explicit parameter, so tests can pin it (worked examples use
  q = 252).
- Volatility: `σ_annual = σ_period × √q`. Mean: `μ_annual = μ_period × q` (arithmetic).
- These are the standard approximations; geometric (CAGR) and arithmetic means are kept separate
  and labelled.

### 4.5 Windows and minimum history

Standard windows: **1Y, 3Y, 5Y, 10Y, Max**, ending at the latest common date. Rolling view: a
1-year rolling window stepped monthly (phase 5).

| Measure | Minimum to show a value |
|---|---|
| Volatility, downside deviation, Sharpe, Sortino | ≥ 52 non-NULL weekly obs (1 year) |
| Max drawdown | 1 year of daily obs (≥ 200 non-NULL) |
| Calmar, CAGR | 1 year, but **flag "short window"** below 3 years |
| VaR/CVaR 95% | ≥ 250 daily obs |
| VaR/CVaR 99% | ≥ 500 daily obs. **Flag `tail_unstable`** below 1,000: CVaR 99% then averages fewer than 10 points. |
| Correlation, beta, covariance | ≥ 104 overlapping weekly obs (2 years) |

> **Amended (RK-13):** tracking error and the information ratio (§5.6) weren't listed. They take
> ≥ 52 overlapping weekly obs, like volatility and Sharpe: tracking error is the volatility of
> the active return and the information ratio its Sharpe.

- **R9.** Below the minimum: show the measure greyed out with "insufficient history (have N,
  need M)", never a number and never a hidden row. **DECISION.**
- **R10. Comparability.** When ranking or comparing several assets or mixes side by side, the
  default is a **common window**: the latest start date among them. The UI names the asset that
  limits the window. An "each over its own max history" view may exist, but must be labelled
  "not comparable".
- **R11. Sampling uncertainty.** Show Sharpe with an approximate standard error (§5.4). Short
  windows make Sharpe noisy: with 3 years of data the SE is roughly ±0.6, at any sampling
  frequency.

---

## 5. Measures on a single return series

All measures are computed on a return series `r_1..r_N` at frequency `q`, with matching risk-free
returns `rf_t` and excess returns `x_t = r_t − rf_t`. Use the sample standard deviation
(denominator N−1). The formulas work at any frequency. §4.3 decides which frequency each
reported figure uses: weekly for §5.2 and the Sharpe and Sortino ratios in §5.4; daily for §5.1,
§5.3 and §5.5. Calmar combines a daily CAGR and a daily MDD.

### 5.1 Return

- **Cumulative return:** `G_N − 1`.
- **CAGR:** `G_N^(1/Y) − 1`, with `Y = calendar_days(start, end) / 365.25`. Don't annualise
  windows shorter than one year; show the cumulative return instead. (This also delivers the
  deferred Labs "CAGR" item.)
- **Annualised arithmetic mean:** `mean(r) × q`. Used only inside Sharpe and Sortino.

### 5.2 Volatility and downside

- **Volatility:** `stdev(r) × √q`.
- **Downside deviation** (target = rf): `√( mean over ALL t of min(x_t, 0)² ) × √q`. The divisor
  is N, all observations, not just the negative ones.

### 5.3 Drawdown

On the growth index with `G_0 = 1` prepended:

- `peak_t = max(G_0..G_t)`
- `DD_t = G_t / peak_t − 1`
- **Max drawdown (MDD)** = `min_t DD_t`, reported as a negative %. Also report:
  - peak date, trough date, and recovery date (first date after the trough where `G ≥` the peak
    value), or "not recovered"
  - **max time under water**: the longest period between a peak and its recovery (or the window
    end), in calendar days
- **Drawdown chart:** the `DD_t` series. A Labs deferred item.
- Drawdowns are path-dependent and window-dependent. A window starting mid-crash understates
  MDD. Always show the window.

### 5.4 Risk-adjusted ratios

- **Sharpe** = `(mean(x) × q) / (stdev(x) × √q)`.
  - Standard error (Lo 2002, i.i.d. approximation) with `SR_p` the per-period Sharpe:
    `SE_annual ≈ √q × √((1 + SR_p²/2) / N)`.
  - Display as `0.86 ± 0.59 (1 s.e.)`. Most readers take a bare `±` as a 95% range, but ±1 SE
    covers only about 68%. The label (or its hover text) says which it is.
  - The i.i.d. approximation ignores fat tails and autocorrelation, which usually widen the
    true SE. Treat it as a lower bound.
- **Sortino** = `(mean(x) × q) / downside_deviation`.
- **Calmar** = `CAGR / |MDD|` over the same window. Flag if the window is under 3 years.

### 5.5 Tail risk (historical simulation, daily)

- **VaR(α), 1-day:** the `(1−α)` empirical quantile of daily returns, reported as a positive
  loss %. α ∈ {95%, 99%}. Use linear interpolation between order statistics; document the method.
- **CVaR(α) / expected shortfall:** the mean of returns at or below the VaR quantile, as a
  positive loss %.
- **Days used:** only days on which every included asset's exchange traded (R7), with NULLs
  excluded (R5). A single asset uses its own trading calendar. Carry-forward zero days would
  otherwise crowd the centre of the distribution and shrink the quantile.
- **Multi-market series** (a mix, the ex-post portfolio, a non-EUR asset in EUR) carry the
  `nonsync_daily` flag and a footnote that the daily figure understates the loss (§4.3).
- Also express both in EUR on the current portfolio market value: "on a bad day (1 in 20) you'd
  lose ≥ €X". **The € figure uses the ex-ante constant-weight series (§6.2)**, because the
  current market value only makes sense with the current mix. Applying an ex-post percentage,
  which reflects past mixes, to today's value would mix two portfolios.
- Never scale 1-day VaR to longer horizons with √t in the UI. Volatility clustering and fat tails
  make it misleading. Out of scope.

### 5.6 Relative to a benchmark (weekly)

- **Benchmark (DECISION):** VWCE in EUR by default, configurable per request. The selected
  benchmark and its start date are shown wherever relative measures appear.
- **One benchmark across all windows.** No silent fallback to another index for long windows.
  VWCE tracks FTSE All-World, which includes emerging markets, and IWDA tracks MSCI World,
  developed only. Swapping between them by window would make 3Y and 10Y beta answer different
  questions. A window the chosen benchmark doesn't cover is greyed, "benchmark history too short
  (from <date>)", as R9 does. To compare over 10Y, pick IWDA explicitly.
  - A longer All-World history is possible: track VWRL (same FTSE index, distributing, listed
    ~2012; **CHECK**), or splice VWCE onto IWDA as a benchmark **named as a splice**. Either is
    a choice for RK-13, never a silent default.
  - **Amended (RK-13):** checked on 2026-10-21. The EUR listing is `VWRL.AS` (Euronext
    Amsterdam), from 2012-05-22, distributing. data-store's `simple_return` adds each dividend
    on its ex-date, so its returns are total returns, comparable with the accumulating VWCE. It
    is offered opt-in: tracking it is a data-store onboarding step, no code, and a request then
    names it. The default stays `VWCE.DE`. No splice was built.
- **Beta** = `cov(r_p, r_b) / var(r_b)` on overlapping weekly EUR returns.
- **Correlation with benchmark** = Pearson's ρ on the same.
- **Tracking error** = `stdev(r_p − r_b) × √52`.
- **Information ratio** = `(mean(r_p − r_b) × 52) / tracking error`.
- **Benchmark line** on the growth chart. A Labs deferred item; this spec covers it as data, and
  the UI is up to the implementer.

### 5.7 Worked example (golden test)

A library test at pinned q, not a statement about which frequency a reported figure uses
(§4.3). Recomputed independently on 2026-10-20; every value matches.

Daily returns `[+1%, −2%, +1.5%, −0.5%, +3%, −4%, +2%]`, constant rf = 0.01% per day, q = 252:

| Output | Value |
|---|---|
| Growth index | 1.010000, 0.989800, 1.004647, 0.999624, 1.029612, 0.988428, 1.008197 |
| Max drawdown | −4.0000% (peak 1.029612 on day 5, trough on day 6, not recovered by day 7) |
| Daily stdev | 2.461610% |
| Annual volatility | 39.0768% |
| Sharpe | 0.8568 |
| Downside deviation (annual) | 27.0867% |
| Sortino | 1.2360 |

---

## 6. Portfolio-level risk

### 6.1 Ex-post (realised)

- **Input:** the whole-portfolio daily TWR growth series (stream 3), converted to returns.
- **Output:** all §5 measures over the standard windows, plus relative measures against the
  benchmark.
- **CHECK (open question for the owner):** does the TWR series include uninvested cash (a
  0%-return asset) or only invested positions?
  - **If it includes cash:** label the result "total account risk". Cash dampens volatility. Also
    provide an "invested only" variant if the series can be built without cash.
  - **If it excludes cash:** label it "invested assets risk".
  - Either way, the label states which one it is.
- **CHECK:** the TWR series is defined on which calendar (union of holdings' trading days?)
  Annualisation uses §4.4's empirical q, so either works.
- **Amended (RK-0):** both CHECKs are resolved. Nothing tracks cash, so the series RK-10 builds
  excludes it, and the label is "invested assets risk". It is on the union of the holdings'
  price dates.
- **Dividends accrue on the ex-date.** The price drops on the ex-date, and portfolio's market
  value is price-only (`position_snapshots.market_value_eur`). But `DIVIDEND` transactions are
  dated when the cash arrives, typically weeks later. A TWR that adds the dividend on its pay
  date shows a false loss on the ex-date and a false gain on the pay date. That inflates
  volatility and drawdown. The TWR must book each dividend on its ex-date, or be built from the
  holdings' total returns (`simple_return`, which already includes the dividend on the ex-date).
  **Agreed (2026-10-20): holdings-based.** `r_t = Σ w_(i,t−1) r_eur,i,t`, with previous-close
  EUR weights. Dividends count gross of withholding tax, and fees and execution prices are
  ignored. RK-10 has the details.

### 6.2 Ex-ante (current holdings, historical simulation)

- **Weights `w`:** current market value per ticker in EUR / total. Holdings are aggregated across
  accounts by ticker. Cash is excluded by default, with a toggle to include it as a 0%-volatility,
  rf-return asset.
- **Returns:** weekly EUR returns per holding (§4.3) over the window.
- **R12. Window for the covariance matrix:** listwise. Use only the weeks where **all** holdings
  have a non-NULL return. The effective start is the latest start among the holdings. The UI names
  the holding that limits it, with an option to exclude that holding and show the longer window.
- **Covariance** `Σ` = sample covariance of weekly returns × 52.
- **Portfolio volatility** `σ_p = √(wᵀ Σ w)`.
- **Constant-weight historical series** `r_p,t = Σ w_i r_i,t`, for drawdown, VaR and Sharpe of
  "today's mix". Use daily frequency for drawdown and VaR, with NULL handling per R5. On a day a
  holding has NULL, that day is excluded for the whole mix. Volatility, Sharpe and Sortino use
  the weekly series (§4.3).
  - **Consistency check:** on the listwise weekly window, the constant-weight series' weekly
    stdev × √52 equals `σ_p` exactly, because sample covariance is bilinear. That's a test, and
    it means the page never shows two volatilities for the same mix.

### 6.3 Risk decomposition (weekly, ex-ante)

For each holding i:

| Output | Formula | Meaning |
|---|---|---|
| Marginal contribution (MCR) | `(Σw)_i / σ_p` | Portfolio volatility change per unit weight |
| Contribution to risk (CR) | `w_i × MCR_i`. The sum over i equals `σ_p`. | Volatility points owed to i |
| % contribution (PCR) | `CR_i / σ_p`. Sums to 100%; can be negative for a hedge. | Share of risk vs share of capital |
| Correlation with portfolio | `(Σw)_i / (σ_i × σ_p)` | How much i moves with the rest |
| Diversification ratio | `(Σ w_i σ_i) / σ_p` | 1 = no diversification; higher is better |
| Effective number of holdings | `1 / Σ w_i²` | Capital concentration |

The UI should show weight % beside risk %. A holding with 10% weight and 25% of the risk is the
headline insight.

**Worked example (golden test).** Weights 60/40, volatilities 20%/10%, correlation 0.3:

| Output | Value |
|---|---|
| σ_p | 13.7405% |
| MCR | 19.2133%, 5.5311% |
| CR | 11.5280%, 2.2124% |
| PCR | 83.898%, 16.102% |
| Correlation with portfolio | 0.96067, 0.55311 |
| Diversification ratio | 1.16445 |

### 6.4 FX decomposition (USD holdings)

For each non-EUR holding, report over the window, on **weekly** returns (daily local and FX
returns are sampled at different times, which biases their correlation toward 0):

- local-currency volatility
- FX volatility (`r_fx`)
- `corr(r_local, r_fx)`
- EUR volatility

This shows how much of the "USD stock risk" is actually the dollar. Phase 5.

---

## 7. Correlation and candidates

### 7.1 Correlation matrix

- **Universe:** current holdings plus any candidates the owner selected, plus the benchmark.
- **Method:** Pearson on weekly EUR returns.
- **R13.** For **display**, each cell uses **pairwise** overlapping weeks, and each cell carries
  its own `n_obs` and start date (tooltip). For **any calculation** (σ_p, risk contributions,
  what-if), use the **listwise** window from R12. A pairwise matrix can be internally
  inconsistent (not positive semi-definite), so it must never feed portfolio math.
- **Options:** window selector (3Y default, 5Y, 10Y, Max) and local-currency vs EUR toggle (EUR
  default). In EUR, two USD assets share the dollar's moves, which raises their correlation. That
  is real risk for a EUR investor.
- **Rolling correlation:** a selected pair, or one asset against the portfolio, over a 1-year
  rolling window of weekly returns (52 obs), stepped weekly. Phase 5. The useful lesson here is
  that correlations rise in crises.
- **Clustering:** order rows by hierarchical clustering of the distance `√(2(1−ρ))`, so
  similar assets sit together. Nice to have.

### 7.2 Candidate what-if

- **Inputs:**
  - a candidate ticker with a usable EUR return series
  - a target weight `w_c` (owner-entered)
  - a **funding rule** (DECISION, default *pro-rata*):
    - pro-rata: scale existing weights by `(1 − w_c)`
    - from cash: only if ex-ante includes cash
    - replace holding X: X's weight moves to the candidate, up to `w_c`
- **Outputs:** before vs after, on the same listwise window. Including the candidate can shorten
  the window; if it does, say so, and recompute "before" on the shortened window so the two are
  comparable.

| Output | Before | After | Δ |
|---|---|---|---|
| Ex-ante volatility | | | |
| Historical Sharpe (constant weights) | | | |
| Max drawdown (constant weights) | | | |
| Diversification ratio | | | |
| Candidate's correlation with current portfolio | — | | |
| Candidate's % contribution to risk | — | | |

- **Rule of thumb to display:** adding a small amount of candidate c improves the historical
  Sharpe if `SR_c > ρ(c, p) × SR_p`. Show the candidate's Sharpe, its correlation with the
  portfolio, and this threshold. It explains *why* a candidate helps or doesn't, using quantities
  already computed.
  - **Pro-rata funding only.** It follows from the derivative of the mix's Sharpe at zero
    weight, when c is funded proportionally from the whole portfolio. Under "replace X" the
    condition involves X too, so the threshold isn't shown in that mode.
  - **Same frequency and window.** `SR_c`, `SR_p` and `ρ` all come from the weekly series on
    the listwise window. Mixing a daily Sharpe with a weekly ρ breaks the identity.
- **Lead with the risk deltas.** Historical means are estimated far less precisely than
  volatilities and correlations (R11: Sharpe ±0.6 at 3 years). So Δσ_p, Δ diversification ratio
  and the candidate's % contribution to risk are the reliable outputs. ΔSharpe is secondary and
  shown with its SE.
- **Ranking mode (optional, phase 3+):** score many candidates at a fixed weight (e.g. 5%) on a
  common window, showing each candidate's history length. Rank by Δσ_p and Δ diversification
  ratio. ΔSharpe is shown, but never the ranking key: picking the best of many noisy historical
  Sharpe deltas is data-mining, and the winner's edge is mostly luck.
- **R14. Survivorship warning.** If a candidate comes from the MSCI World constituent universe
  (rather than `tracked_tickers`), show: "Historical figures come from today's index members and
  overstate what an investor could have achieved." Ranking mode shows it once at the top.
- **R15.** Historical Sharpe and mean returns are **not forecasts**. The UI copy says
  "historical", never "expected".

---

## 8. Concentration (look-through)

- **Exposures:** by **currency**, **sector**, **country** and **asset class**. Weight = sum over
  holdings of `w_holding × w_constituent`.
- **Sources:**
  - Direct stocks: their own sector, country and currency, taken from the latest `etf_holdings`
    snapshot if the stock is a constituent, otherwise "unknown".
  - IWDA (iShares MSCI World): the latest `etf_holdings` snapshot.
  - Other ETFs (VWCE, EM IMI, Small Cap), with no holdings data: one "unmapped: <ETF>" bucket.
    The unmapped share is shown prominently.
  - ETCs: asset class "commodity". Currency is the underlying pricing currency (USD for
    gold/silver), not the listing currency.
- **R16. Currency exposure ≠ trading currency.** A EUR-listed world ETF is mostly USD exposure.
  The currency view uses the constituents' `currency`, with the listing currency shown alongside.
  For a EUR investor this is the most important concentration view.
- **Single-name overlap.** Also aggregate look-through by **issuer**: a stock held directly
  plus through IWDA is one exposure. For an investor holding single stocks beside a world ETF,
  the top look-through names are often the most actionable view.
- **Currency-hedged share classes.** A EUR-hedged ETF's constituent currencies aren't its
  currency exposure. Until the hedge is known per holding, a hedged ETF goes into the currency
  view as "hedged: <ETF>" rather than through its constituents. **CHECK** whether any holding
  is hedged, and how that can be known (name suffix, a mapping).
- **Constituent currency is a proxy.** It's the stock's trading currency, not where its revenue
  comes from. Label it "listing currency of holdings".
- **Concentration metrics:** top-N weights, and the Herfindahl index (`Σ w²`) with its inverse as
  "effective number" per dimension.
- **Snapshot date:** shown. Look-through is a point-in-time view only; there's no history of
  index composition.

---

## 9. Boundaries and computation (guidance, not prescription)

- **Pure library:** a pure, side-effect-free risk library takes numpy/pandas series plus
  parameters and returns typed results. Results carry `value`, `window_start`, `window_end`,
  `frequency`, `q`, `n_obs`, `n_gaps` and `flags` (e.g. `insufficient_history`, `short_window`,
  `has_suspect_rows`, `rf_partial`, `survivorship`, `nonsync_daily`, `tail_unstable`).
  - All golden tests live against this library.
- **data-store:** owns the risk-free series, EUR conversion and weekly EUR returns. Weekly EUR
  returns can be materialised; that's optional, since ~1,300 tickers is small.
  - **Amended (RK-0):** EUR conversion and weekly EUR returns stay in portfolio, which already
    owns the conversion (Labs). Moving it would leave two copies that must agree. data-store
    owns the risk-free series.
- **portfolio service:** owns ex-post series, current weights, look-through and what-if.
- **Computation:** on demand is fine at this scale (12 holdings plus a handful of candidates).
  Cache keyed by `(tickers, weights, window, as_of)`.
- **Point-in-time:** all calculations use data on or before `as_of`. There's no look-ahead,
  including in the risk-free rate.

---

## 10. UI rules (domain-level)

- Every number shows, or has on hover, its window, frequency, `n_obs` and ex-post/ex-ante label.
- Percentages: volatility, drawdown and VaR to 1 decimal; ratios to 2 decimals; Sharpe with
  ± SE, labelled "1 s.e." (§5.4).
- Insufficient data → greyed with the reason (R9). Never 0, never blank.
- Labs panel (delivers the deferred "volatility and max drawdown"):
  - CAGR, volatility, MDD, Sharpe, Sortino and Calmar for each standard period
  - a drawdown chart
  - a benchmark line on the growth chart
- Portfolio risk page:
  - ex-post summary
  - ex-ante summary
  - risk decomposition table
  - correlation heatmap
  - concentration
  - candidate what-if

---

## 11. Phased task plan

**Phase 0: discovery (CHECK items, no code changes)**

1. Does the portfolio TWR series include uninvested cash? Which calendar is it on?
2. How is the per-ticker TWR built, and over what dates?
3. Locate the Labs EUR-conversion and NULL-bridging code. Confirm it can be reused without the
   bridging.
4. Confirm the VWCE and IWDA tickers, listing currency and first date in `market_returns`.
5. Report findings and adjust §6.1 labels accordingly.

*Amended (RK-0):* done. The findings are in the task thread's "Discovery" section. Items 1, 3
and 5 are amended in place above. For item 2: there is no per-ticker TWR. For item 4: `VWCE.DE`
is EUR-listed from 2019-07-29, and `IWDA.AS` is EUR-listed from 2009-09-25.

**Phase 1: inputs**

1. Ingest €STR and EONIA, build the spliced `rf_eur` daily series (§4.1), and add an Airflow
   daily update.
2. Shared EUR-return function (§4.2), with an "ineligible: no FX" result.
3. Weekly aggregation (R6) and the gap accounting (R8).
4. Tests: §4.2 FX example; a week containing a NULL becomes NULL; rf compounding over a weekend.

**Phase 2: single-series measures**

1. Risk library: §5.1–5.5, with result metadata and flags.
2. Golden test §5.7.
3. Labs risk panel: per-period metrics, drawdown chart, CAGR.
4. Ex-post portfolio risk summary.

**Phase 3: correlation, ex-ante and candidates**

1. Benchmark config and relative measures (§5.6).
2. Correlation matrix: pairwise for display, listwise for math (R13).
3. Ex-ante σ_p and risk decomposition (§6.2–6.3). Golden test §6.3.
4. Candidate what-if with funding rules (§7.2), the Sharpe threshold, and the survivorship flag.

**Phase 4: concentration**

1. Look-through exposures (§8), with the unmapped bucket and currency-vs-listing view.

**Phase 5: depth**

1. Rolling 1Y volatility, Sharpe and correlation charts.
2. FX decomposition (§6.4).
3. Regime view: for each `regimes` row overlapping the window, the portfolio's and benchmark's
   return and MDD within that period. Use `known_from` only if regimes ever become a signal; for
   descriptive use it isn't needed.
4. Candidate ranking mode.
5. Optional: Ledoit–Wolf shrinkage of Σ when assets exceed ~¼ of the weekly observations.

---

## 12. Decisions taken on the owner's behalf (override any)

| # | Decision | Default |
|---|---|---|
| D1 | Risk-free rate | €STR/EONIA splice; 0% before 1999, flagged |
| D2 | Benchmark | VWCE in EUR, configurable; one benchmark across all windows, uncovered windows greyed (§5.6) |
| D3 | Frequencies | Daily for return, CAGR, drawdown and VaR; weekly for volatility, Sharpe, Sortino, correlation, covariance and beta (§4.3) |
| D4 | Windows | 1Y/3Y/5Y/10Y/Max; rolling 1Y in phase 5 |
| D5 | Insufficient data | Greyed with reason, using the minimums in §4.5 |
| D6 | NULL in risk path | Excluded; Labs growth chart unchanged |
| D7 | Ex-post and ex-ante | Both, always labelled |
| D8 | FX | EUR returns by default; FX decomposition in phase 5 |
| D9 | Candidates | Any ticker with an EUR series; owner-entered weight; pro-rata funding |
| D10 | Concentration | Phase 4; regimes in phase 5 |

---

## 13. Revisions from the 2026-10-20 domain review

The golden tests (§5.7, §6.3) and the §4.2 FX example were recomputed independently and are
unchanged. The rules below were revised in place:

| Where | Change | Why |
|---|---|---|
| §4.3, §4.5, §5, §6.2, D3 | Volatility, downside deviation, Sharpe and Sortino moved from daily to weekly | The daily non-synchronous bias that §4.3 fixed for correlation also understates the volatility of any multi-market or FX-converted series, and it made the daily mix volatility disagree with weekly σ_p |
| §4.3, §5.5 | Daily VaR flagged `nonsync_daily` on multi-market series | Same bias, but VaR has to stay 1-day |
| R7, §5.5 | VaR uses only days on which every included asset traded | Carry-forward zeros shrank the tail quantile |
| R5 | A NULL is stated to be a lost move | Confirmed against data-store: NULLs are suppressed implausible returns, and the real move across them isn't recoverable |
| §4.5 | `tail_unstable` flag on VaR/CVaR 99% below 1,000 obs | CVaR 99% on 500 obs averages ~5 points |
| §5.4, §10 | Sharpe ± labelled "1 s.e."; the i.i.d. SE called a lower bound | A bare ± reads as a 95% range |
| §5.5 | € VaR uses the ex-ante constant-weight series | Today's market value only pairs with today's mix |
| §5.6, D2 | No IWDA fallback; one benchmark across all windows | VWCE and IWDA track different indices, so a fallback would change the question by window |
| §6.1 | Dividends accrue on the ex-date in the TWR | Portfolio market value is price-only and dividends are dated on payment, so pay-date booking creates false swings |
| §6.4 | FX decomposition is weekly | Local and FX returns are sampled at different times |
| §7.2 | Sharpe threshold only under pro-rata funding, on one frequency; risk deltas lead; ranking by Δσ_p and Δ diversification ratio | The threshold's derivation assumes pro-rata funding; historical Sharpe deltas are mostly noise |
| §8 | Single-name overlap, hedged share classes, "listing currency" label | Missing views and a wrong-answer case |
