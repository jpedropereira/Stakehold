# portfolio risk and Labs flows

The "Ex-post risk" and "Labs backtest" sections of [`flows.md`](flows.md), moved here verbatim to
keep that file under the repo's 64KB limit (RK-13). Later risk flows belong here too: ex-ante
risk (RK-17) is, and the what-if and rolling windows will be. Their rows in the flow table, and the HTTP API's notes
on them, stay in `flows.md`. "Above" and "below" in the sections here refer to this file;
references to sections that stayed in `flows.md` link to it.

## The flows

### Relative to a benchmark

Both flows below measure against one benchmark (RK-13, spec §5.6), named per request and
`VWCE.DE` by default: `?benchmark=` on `GET /risk/ex-post`, `"benchmark"` in the backtest's
body. It is a ticker of 1 to 20 characters; anything else is a 422. It isn't saved.

`benchmark/flows.fetch_benchmark` fetches its ticker info, its whole price history and, outside
EUR, its FX series, through `eur_returns/fetch.py`, and turns them into its daily EUR total
returns and their W-FRI weeks. Each covered window then gets a `relative` object:

    {"beta", "correlation", "tracking_error", "information_ratio"}

each a figure in the usual shape, all `frequency: "weekly"` on the weeks both the series and the
benchmark have a return for, among the whole weeks ending inside the window. A NULL week on either
side is a gap.

- **Minimums.** Beta and correlation need 104 weeks (spec §4.5). Tracking error and the
  information ratio need 52, like volatility and Sharpe: they are the active return's volatility
  and Sharpe (agreed for RK-13). So a 1Y window, 52 or 53 weeks, never has a beta.
- **A window the benchmark doesn't cover**, one starting before its first day, has all four
  greyed, "benchmark history too short (from 2019-07-29)". Nothing is measured against another
  index in its place. To compare over 10Y, name `IWDA.AS` (from 2009) or `VWRL.AS` (FTSE
  All-World like VWCE, from 2012).
- **Undefined is greyed**: "no variation in benchmark returns" (beta, correlation), "no
  variation in returns" (correlation), "no tracking difference" (the information ratio, when
  tracking error is exactly 0, which is then shown).
- **An unavailable benchmark** is reported, not fatal. Its status is one of "not tracked", "no
  history yet", "no FX series for <CCY>" or "couldn't reach data-store" (the exception is
  logged, not returned), and every relative figure is greyed with "benchmark unavailable:
  <reason>". Every other figure is measured as before, and the response is still a 200.
- The response names the benchmark: `{"ticker", "available", "reason", "first_date"}`, where
  `first_date` is the date its first return is measured from.
- `benchmark/flows.py` logs one line: the returns it fetched and their span, a warning with the
  reason, or an error with the exception.

### Ex-post risk

The §5.1–5.5 risk figures on the portfolio TWR (RK-11; spec §6.1; see [`flows.md`](flows.md)'s
"Portfolio TWR"), labelled "ex-post · invested assets": cash isn't tracked, so they measure the
invested positions only.
`risk/flows.compute_ex_post` runs `twr/flows.compute_portfolio_twr` (same reads, fetches and
exclusions), fetches data-store's risk-free series from two weeks before the TWR's base date to
its last date and the benchmark up to that date, converts them to floats, and hands them to
`risk/ex_post.py`. The response is

    {"label", "base_date", "end_date", "rf_available", "benchmark",
     "windows": [{"label", "start", "end", "covered",
                  "cumulative_return", "cagr", "volatility", "downside_deviation", "sharpe",
                  "sortino", "max_drawdown", "peak_date", "trough_date", "recovery_date",
                  "max_days_under_water", "calmar", "var_95", "cvar_95", "var_99", "cvar_99",
                  "relative"},
                 ...],
     "n_excluded", "excluded": [{"ticker", "status", "reason"}, ...]}

Each figure is `{value, window_start, window_end, frequency, q, n_obs, n_gaps, flags, reason,
standard_error}`, the metadata spec §9 and §10 ask every number to carry.

- **Windows** are 1Y, 3Y, 5Y, 10Y and Max, all ending at `end_date`, the TWR's last date. Max
  starts at `base_date`. A window that starts before `base_date` has `covered: false`, and every
  figure in it is greyed with "insufficient history (portfolio history from <date>)".
- **Frequencies** follow spec §4.3: cumulative return, CAGR, drawdown and VaR are daily;
  volatility, downside deviation, Sharpe and Sortino are weekly, on the whole Friday-to-Friday
  weeks ending inside the daily window, so a 1Y figure has 52 weeks.
- **A greyed figure** has `value: null` and a `reason`: below its §4.5 minimum, an undefined
  ratio, an uncovered window, or "no risk-free rate data" when `rf_available` is false.
- **VaR and CVaR** keep only the dates on which every ticker held the day before traded, and
  carry `nonsync_daily` unless the window held exactly one EUR ticker.
- **`excluded`** is the TWR's, with the same statuses and wording.
- **`relative`** is the weekly TWR against the benchmark ("Relative to a benchmark" above). It
  is always there: in a window the portfolio doesn't cover, its figures are greyed with the same
  reason as the rest.

With no TWR returns (no accounts, no rows, or every holding left out) the response has no
windows, not a 404, and no risk-free or benchmark fetch is made: `benchmark` is
`available: false`, "not fetched: nothing to measure". A failed risk-free fetch isn't a 502: it
greys the three figures that need the rate, and the exception is logged. The flow logs the start
(naming the benchmark), one line per window (measured with its daily obs and how many figures
are greyed, then how many of the four relative ones, or greyed and why) and the risk-free
outcome; the job logs a start line and a summary naming the benchmark and its first date, or why
it was unavailable. The rules' reasons are
in `architecture.md`'s `risk/` row.

### Ex-ante risk

The mix held at constant weights through history (RK-17; spec §6.2–6.3, §7.1), labelled
"ex-ante · constant weights". `POST /risk/ex-ante` takes the mix as Labs' backtest does,
`{assets: [{ticker, weight}], window, benchmark, currency, exclude}`, because web builds it
from the holdings with `labs_view.build_default_mix`: the weights are worked out in one place,
and the what-if (RK-19) can send a changed mix in the same shape. No user data is read.
`window` is one of 1Y, 3Y, 5Y, 10Y and Max (default 3Y, spec §7.1's), `currency` is `eur`
(default) or `local`, and `exclude` names tickers from the mix to leave out. A ticker in
`exclude` but not in the mix, or an `exclude` that leaves nothing, is a 422.

`risk/flows.compute_ex_ante` fetches each asset's whole history through
`eur_returns/eligibility.py` (the TWR's fetch and wording, lifted out for this), the risk-free
series from two weeks before the oldest holding's first day, and the benchmark. It normalises
the weights over the included holdings and hands the daily EUR returns to
`risk/ex_ante_report.py`. The response is

    {"label", "currency", "rf_available", "benchmark",
     "window": {"label", "requested_start", "start", "end", "limited_by"},
     "statuses": [{"ticker", "status", "reason"}, ...],
     "sigma_p", "diversification_ratio", "effective_n",
     "holdings": [{"ticker", "weight", "volatility", "mcr", "cr", "pcr",
                   "corr_with_portfolio"}, ...],
     "constant_weight": {the twelve ex-post figures and the max drawdown's dates},
     "correlation": {"keys", "benchmark_key", "cells": [{"a", "b", "figure"}, ...]}}

- **One requested window, three uses.** It ends on the holdings' latest common date, and Max
  starts on the *oldest* holding's first day. The decomposition runs on its listwise weekly
  panel (R12), which a younger holding shortens; `limited_by` names that holding, so the UI can
  offer to exclude it, and `start` is where the panel starts. The constant-weight figures run
  from `start` to `end`, so their weekly volatility is `sigma_p` exactly (§6.2's check, tested):
  the page never shows two volatilities for one mix. The correlation matrix keeps the requested
  window, each pairwise cell over the weeks that pair shares (R13), so a young holding doesn't
  shorten the other cells.
- **Minimums** are RK-15's: `sigma_p` greys under 52 weeks, the diversification ratio and every
  `holdings` figure under 104 (so 1Y never has a decomposition). `effective_n` never greys.
- **Statuses**, in request order: `included`, `excluded` ("excluded by request", before any
  fetch), or left out as the TWR words it: `not_tracked`, `no_history`, `no_fx_rate` ("no FX
  series for <CCY>") or `failed` ("couldn't reach data-store", the exception logged).
- **The matrix** is the included holdings in request order, then the benchmark. A benchmark
  that is also a holding isn't repeated, and `benchmark_key` names its row either way. An
  unavailable one has no row (`benchmark_key: null`), and `benchmark` says why. `cells` is the
  upper triangle, diagonal included, row by row. With `currency: local` its cells use each
  ticker's local-currency returns (RK-5's `r_local`). `sigma_p` and the decomposition stay EUR.
- **The constant-weight figures** follow the ex-post rules: VaR on the days every holding
  traded, `nonsync_daily` unless the mix is one EUR holding, and no risk-free data greying
  downside deviation, Sharpe and Sortino. Holdings whose histories don't overlap grey them all,
  "the holdings' histories don't overlap", and `window.start` is null.
- **The € tail figures are web's**: it knows the market value, and portfolio returns fractions.

With nothing included (every asset excluded or left out), the response is a 200 with `statuses`
and `benchmark` ("not fetched: nothing to measure") and every other section null. No risk-free
or benchmark fetch is made. The flow logs the start (asset count, exclusions, window, benchmark,
currency), one line per asset, the window (requested and measured, and what limited it), and a
summary of how many figures and cells are greyed. The job logs a start line and a done line.

**Size and time, synthetic worst case** (every asset on 16.8 years of weekdays, half in USD,
Max): 12 assets give 25 KB in 0.18 s; 50 assets give 294 KB in 1.1 s, almost all of it the
1,326 correlation cells.

### Labs backtest

How a mix of assets, bought at its weights and held, would have done over 1M, 3M, 6M, 1Y, 2Y,
5Y and Max, in EUR (LB-4). The request is

    {"assets": [{"ticker", "weight"}, ...], "period": "1M" | ... | "Max",
     "benchmark": "VWCE.DE"}

and the response is

    {"end_date", "mix": Row | null, "assets": [Row, ...], "growth": Growth | null,
     "statuses": [{"ticker", "status", "reason"}, ...], "risk": Risk | null}

where a `Row` is `{ticker, weight, periods: {"1M": {start, end, covered, return_pct, gaps,
left_out}, ..., "Max": ...}}` and `Growth` is `{period, dates, mix, assets: {ticker: [...]},
left_out}`. Every figure is a fraction (0.10 is +10%), written in fixed-point. The arithmetic
and its rules are in `labs/calculator.py`'s docstring.

- **The mix holds the assets that cover each period (LB-9).** An included asset whose history
  starts after a period's start is left out of the mix for that period, the rest are
  re-weighted to 100%, and `left_out` names it, on the mix's period and on `growth`. It is
  always empty on an asset's row, and never lists an asset that isn't `included` (its status
  says why). The mix has no figure, and no growth line, only when no asset covers the period.
- **Max is each asset's whole history.** An asset's Max `start` is its own first day; the
  mix's is the earliest one. A later asset joins the mix on its first day, when the mix is split
  again by the target weights. Its Max growth line is null on the dates before that day, so
  every line still has one value per date.
- The job logs one line per asset the mix left out, with the periods: "the mix leaves out BRK-B
  for 3M, 6M, 1Y, 2Y, 5Y: not enough history."

**The risk section (RK-8)** is null exactly when no asset is included. Otherwise it is

    {label: "buy & hold (drifting)", end_date, common_start, limited_by: [ticker],
     risk_free: {available, reason, first_date},
     benchmark: {ticker, available, reason, first_date, growth, growth_reason},
     mix: {ticker: null, windows: {"1Y": Window, "3Y", "5Y", "10Y", "Max"}},
     assets: [{ticker, windows}, ...], drawdown: {period, dates, values} | null}

where a `Window` is `{start, end, covered, reason}` plus a figure for each of
`cumulative_return`, `cagr`, `volatility`, `downside_deviation`, `sharpe`, `sortino`, `calmar`,
`max_drawdown`, `var_95`, `cvar_95`, `var_99`, `cvar_99`, and the drawdown's `peak_date`,
`trough_date`, `recovery_date` and `max_days_under_water`, and `relative` (RK-13). Each figure is `{value, window_start,
window_end, frequency, q, n_obs, n_gaps, flags, reason, standard_error}` (risk spec §9). Unlike
the rest of the response, the figures are JSON numbers, not fixed-point strings: they are
estimates, not ledger amounts. The rules, argued in `labs/risk.py`'s docstring:

- **The windows are the spec's**, not Labs' periods, all ending on `end_date`. A window starting
  before `common_start`, the youngest asset's first day (`limited_by`), is `covered: false` on
  **every** row, with "limited by X (history from <date>)" and every figure null. Max always
  starts on `common_start`. When the histories don't overlap at all, `common_start` is null and
  every window is uncovered.
- **Inside a covered window, a figure greys on its own**: `value` null and `reason` set, below its
  minimum ("insufficient history (have 170, need 200)") or when undefined ("no drawdown in
  window"). Never 0.
- **The mix is bought on each window's start and drifts.** A day on which any asset's return is
  NULL is a gap in its figures (`n_gaps`), while Labs' period table and growth line still bridge
  it as a flat day.
- **Volatility, downside deviation, Sharpe and Sortino are weekly** (`frequency: "weekly"`, q =
  52, on the whole Friday-to-Friday weeks ending inside the window). Everything else is daily.
  VaR counts only days every asset has a price, and carries `nonsync_daily` whenever the row includes a
  non-EUR asset.
- **`relative`** is each row against the benchmark ("Relative to a benchmark" above): the mix's
  weekly series re-based at the window's start, or the asset's own. It is null exactly when the
  window isn't covered. The benchmark doesn't move `common_start`: it isn't a row.
- **`benchmark.growth`** is the benchmark's line for the growth chart: one fixed-point value per
  `growth.dates` entry, from 0, priced as an asset's line is (gaps bridged as flat days, FX on
  or before each date). It is null, with `growth_reason`, when the benchmark is unavailable or
  starts after `growth.dates[0]` ("benchmark history too short (from <date>)"): started later
  on its own base it would be on a different scale from the mix's.
- **`drawdown`** is the mix's only, one value per `growth.dates` entry, null where an asset's
  return was NULL. It is null when `growth.mix` is.
- **The risk-free series** comes from one more call, `get_risk_free_series`, made after the price
  fetch over the backtest's span (ten days before `common_start` to `end_date`), and skipped when
  the histories don't overlap. When data-store has no rates stored, the call fails, or no rate is
  dated on or before `common_start`, `risk_free.available` is false, and downside deviation,
  Sharpe and Sortino are greyed with its `reason` ("no risk-free rate data", "couldn't reach
  data-store", "no risk-free rate on or before <date>"). The backtest still answers 200.
- `labs/flows.py` logs a start line (assets, end, common start, limiting asset), the risk-free
  outcome (an error with the exception when the call raised, a warning when nothing is stored),
  one line per mix window ("mix 3Y: not covered, limited by VWCE.DE (history from 2019-07-29)."
  or "mix 1Y: measured 2025-10-17..2026-10-16, 2 of 16 figure(s) greyed.", the relative four
  included), one line per asset with how many windows it is measured in, and a summary naming
  the benchmark, or why it was unavailable. The job's start line names it too.

`labs/flows.run_backtest` fetches through `eur_returns/fetch.fetch_series` (shared with the
portfolio TWR since RK-10), in two concurrent phases, through a pool of 8 threads:

1. `get_ticker_info` for every asset.
2. `get_price_series` for every asset that exists and has returns, and `get_fx_rate_series`
   once per distinct non-EUR `fx_currency`, shared by every asset in that currency.

Both series run from 1970-01-01 to today, since Max needs each asset's whole history. The FX pair
needs the ticker info first, so the phases can't overlap.

- **`statuses`** has every requested asset, in request order, each with one of:
  - `included`, with no reason.
  - `not_tracked` ("not tracked"): data-store has never ingested it.
  - `no_history` ("no history yet"): it has no returns yet, which means it is still onboarding,
    or its price series is empty.
  - `no_fx_rate` ("no FX rate"): it isn't in EUR, and it has no `fx_currency`, or that
    currency's series is empty or starts after its last price.
  - `failed` ("couldn't reach data-store"): one of its calls raised. The exception is logged,
    not returned.

  A left-out asset is dropped from the mix, and the rest are re-weighted to 100%. `assets` lists
  only the included ones, with the weight actually used. When none is included, `end_date`,
  `mix` and `growth` are null.
- **Failures stay with their asset.** Each call is caught on its own. A failed FX fetch fails
  every asset in that currency and no other.
- **Validation is the route's.** A 422 is returned for an empty mix, more than 50 assets, a
  duplicate ticker, a weight that isn't a positive finite number, a ticker that is empty or
  longer than data-store's 20 characters, or an unknown period.
- **The cap of 50** sits above any realistic personal portfolio, because web's default mix is the
  user's whole holdings. It still bounds one request to about 106 data-store calls, and a Max
  growth series to one point per trading day for each of 50 assets.

It is a POST because the mix is a structured body. It changes nothing, so it answers 200. The
mix holds no user data, so there is no `user_id`.
