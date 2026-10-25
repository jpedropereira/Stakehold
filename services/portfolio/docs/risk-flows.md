# portfolio risk and Labs flows

The "Ex-post risk" and "Labs backtest" sections of [`flows.md`](flows.md), moved here verbatim to
keep that file under the repo's 64KB limit (RK-13). Later risk flows belong here too: ex-ante
risk (RK-17), look-through exposures (RK-22) and rolling windows (RK-24) are, and the what-if will be. Their rows in the flow table, and the HTTP API's notes
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
     "n_excluded", "excluded": [{"ticker", "status", "reason"}, ...],
     "rolling": {"window": "1Y", "step": "monthly", "volatility": [figure, ...],
                 "sharpe": [figure, ...]}}

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
- **`rolling`** (RK-24) is the TWR's rolling 1Y volatility and Sharpe, one figure per month end
  ("Rolling windows" below). It is null exactly when there are no windows.

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
and the what-if (RK-19) takes its mix in the same shape. No user data is read.
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
     "correlation": {"keys", "benchmark_key", "cells": [{"a", "b", "figure"}, ...]},
     "rolling": {"window": "1Y", "step": "monthly", "volatility": [...], "sharpe": [...]},
     "rolling_correlation": {"a", "b", "window": "52W", "step": "weekly", "reason",
                             "points": [figure, ...]} or null,
     "fx_decomposition": [{"ticker", "currency", "local_volatility", "fx_volatility",
                           "correlation", "eur_volatility"}, ...]}

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
- **`rolling`** (RK-24) is the constant-weight mix's rolling 1Y volatility and Sharpe over the
  mix's **whole common history, whatever `window` asks for**, since a rolling view is for
  watching the figure move. The request's optional `rolling_correlation: {a, b}` asks for one
  pair's rolling ρ: two tickers, each from the mix or the benchmark, or `a` against the
  portfolio when `b` is null. A ticker that is in neither, a ticker `exclude` leaves out, or
  a == b is a 422. The response's `b` is then `"portfolio"`. The pair is always in EUR,
  whatever `currency` says, because "the portfolio" is the EUR constant-weight weekly mix (with
  the asset in it, as `corr_with_portfolio` has it) and web has no currency toggle. A side that
  passed validation but can't be measured gives empty `points` and a `reason`, never an error:
  "<ticker> not included: <its status reason>", "<benchmark> not included: benchmark
  unavailable: <reason>", or "portfolio not included: the holdings' histories don't overlap".
  `rolling_correlation` is null when no pair was asked for.
- **`fx_decomposition`** (RK-26, spec §6.4) has one row per included non-EUR holding, in request
  order: the volatility of its local-currency returns, of its currency against the EUR (r_fx),
  their correlation, and the volatility of its EUR returns. It's empty when every holding is
  EUR, whose r_fx is 0. The flow takes each holding's RK-5 `r_local` and `r_fx` from the same
  `EurReturns` as its EUR series, so nothing more is fetched. Decisions (agreed):
  - **The rows run on the decomposition's listwise panel**, the same weeks as `sigma_p`, so a
    row's `eur_volatility` is that holding's `holdings` volatility exactly (tested), and the page
    never shows two volatilities for one holding. A younger holding shortens the rows as it
    shortens the decomposition, and the same exclude link lengthens both. *Rejected: each
    holding over its own weeks in the requested window*, as a heatmap cell is. That shows a
    longer history, but it would disagree with the decomposition table beside it.
  - **Weekly, compounded from the daily components.** Per day (1 + r_local)(1 + r_fx) =
    1 + r_eur, and compounding the same days keeps this per week, so the three volatilities
    describe one return. A holding's weekly local return is NULL exactly where its EUR one is,
    so the panel's weeks have all three.
  - **Minimums:** the volatilities grey under 52 weeks and the correlation under 104 (§4.5), so
    1Y always greys the correlation. Between 52 and 104 weeks, `eur_volatility` is shown while
    the `holdings` volatility is null: that one is part of the decomposition, which takes 104.
    A pegged currency greys the correlation ("no variation in returns").
  - **Served on this endpoint, not a sibling** (*rejected: `POST /risk/fx-decomposition`*,
    which would fetch every holding again and make web call twice), as RK-24 added `rolling`.
  - **No derived "FX share of risk"**: the EUR variance is σ²_local + σ²_fx + 2ρσ_localσ_fx plus a
    cross term, so no split into shares adds up.

With nothing included (every asset excluded or left out), the response is a 200 with `statuses`
and `benchmark` ("not fetched: nothing to measure") and every other section null. No risk-free
or benchmark fetch is made. The flow logs the start (asset count, exclusions, window, benchmark,
currency), one line per asset, the window (requested and measured, and what limited it), and a
summary of how many figures and cells are greyed, then one line for the FX decomposition: how
many non-EUR holdings it covers and how many of their figures are greyed, or that every included
holding is in EUR. The job logs a start line and a done line.

**Size and time, synthetic worst case** (every asset on 16.8 years of weekdays, half in USD,
Max): 12 assets give 25 KB in 0.18 s; 50 assets give 294 KB in 1.1 s, almost all of it the
1,326 correlation cells. RK-24 added 77 KB of `rolling` (191 monthly points of two figures)
and, with a pair, 161 KB of `rolling_correlation` (823 weekly points). Re-measured with both:
12 assets give 267 KB in 0.31 s, and 50 give 564 KB in 1.3 s. Every point carries its full
figure metadata, as every other figure does. If the size starts to matter for web, the
repeated `frequency` and `q` are the first thing to hoist out.

### Rolling windows

RK-24, spec §4.5 and §7.1, computed by the pure `risk/rolling.py` and served on the two
endpoints above. Nothing new is fetched: ex-post reuses the TWR, ex-ante the mix, and both reuse
their risk-free and benchmark fetches.

- **Rolling 1Y volatility and Sharpe, stepped monthly.** Each window ends on the last date the
  series has in a calendar month and starts one calendar year earlier (29 February clamps to
  the 28th). The windows are drawn from the daily series, the TWR or the daily mix, and measured
  on its weekly form. The last one ends on the series' last date, even mid-month, so it **is**
  the 1Y window and its figures equal the 1Y ones (tested on both endpoints). The first is the
  first full year. Months before that have no point at all, because they have no 1Y window,
  only a shorter window that the 1Y label would misdescribe. Each point is
  `single_series.weekly_figures` over its window, so whole weeks, risk-free, flags and greying
  are the standard figure's by construction, not a second copy. With no risk-free series,
  Sharpe is greyed with "no risk-free rate data" and volatility is kept.
- **Rolling correlation, 52 weeks stepped weekly.** One point per Friday both sides have, from
  the first with 52 whole weeks of both, each `correlation.pair_cell` over `F − 52 weeks .. F`.
  **Its minimum is 52 weeks** (agreed for RK-24; spec §4.5 "Amended (RK-24)"), not §4.5's 104
  for correlation, under which every point of a 52-week window would be greyed. It is for
  display, as a pairwise cell is (R13), and never feeds portfolio maths. *Rejected: a 104-week
  rolling window*, which keeps §4.5's minimum but smooths away the short crisis spikes that
  §7.1 says are the view's lesson. Its points are noisy, with an SE of about 0.14 at 52 obs.
- **A window below its minimum is a gap, not a point** (RK-24): the figure is greyed with R9's
  reason, `value: null`, and it keeps its place, so web breaks the line there instead of
  joining across it. **Sharp edge:** a calendar year holds 52 or 53 whole weeks and the weekly
  minimum is 52, so a single NULL week (a TWR date out of the market, or a holding's NULL day)
  can turn a 1Y point into a gap. The standard 1Y window has the same edge. Lowering the
  minimum to fit was rejected, as it was for RK-7: it would bend the spec to the data.
- **Logging:** each flow logs one rolling line, the monthly point count and how many volatility
  and Sharpe points are greyed, or "no point, the history is under a year". Ex-ante also logs
  the pair's weekly point count and gaps, or why it has none.

### Candidate what-if

Today's mix before and after adding a candidate (RK-19; spec §7.2), labelled "ex-ante · constant
weights" like ex-ante, since it is ex-ante risk of two mixes. `POST /risk/what-if` is a sibling
of `POST /risk/ex-ante` rather than an option on it (agreed): its response is a before/after
table, not a decomposition and a matrix, and it needs no benchmark. It takes

    {assets: [{ticker, weight}], candidate: {ticker, weight}, funding, window, exclude}

`assets`, `window` and `exclude` are ex-ante's. `candidate.weight` is a fraction of the whole mix
*after* the change, strictly between 0 and 1. `funding` is `pro_rata` (the default) or
`replace:<ticker>`. "From cash" is deferred until cash is tracked. A 422 is only for a malformed
request: a candidate already in the mix, a weight outside (0, 1), a replace target not in the
mix or excluded, or ex-ante's own mix rules. `risk/flows.compute_what_if` fetches the holdings
and the candidate in one `eur_returns/eligibility.py` call, normalises the holdings' weights
over those included, funds the candidate, and hands the daily EUR returns to
`risk/what_if.py`. The response is

    {"label", "rf_available", "statuses": [...],
     "candidate": {"ticker", "status", "reason", "requested_weight", "weight", "capped",
                   "survivorship"},
     "funding": {"rule", "ticker", "reason"},
     "window": {"label", "requested_start", "start", "end", "limited_by", "shortened",
                "start_without_candidate", "end_without_candidate"},
     "weights": {"before", "after"},
     "rows": {"sigma_p", "diversification_ratio", "candidate_pcr", "candidate_correlation",
              "sharpe", "max_drawdown"},            # each {"before", "after", "delta"}
     "threshold": {"applicable", "reason", "candidate_sharpe", "correlation",
                   "portfolio_sharpe", "threshold", "improves"}}

- **"Before" holds the candidate at weight 0** (agreed), so both mixes run on one listwise panel
  (R12) with the same window and the same weeks, and the candidate's NULL weeks are dropped from
  "before" too. This is what §7.2 means by "on the same listwise window". If the candidate starts
  later (or ends earlier) than the holdings, `shortened` is true, `limited_by` names it, and
  `start_without_candidate` says where the holdings alone would have started. "Before" is then
  the holdings on the shortened window, not the ex-ante section's figures. To allow this,
  `ex_ante.py` changed RK-15's rule: a weight may be 0 (it was "each above 0"). *Rejected: a
  second panel-restriction path for "before"*, which would have duplicated R12's logic. The
  zero weight also gives the candidate's correlation with the portfolio it would join for free:
  its `corr_with_portfolio` in "before"'s decomposition.
- **Funding.** Pro-rata scales every holding by `(1 − w_c)`. Replacing X moves up to `w_c` of
  X's weight to the candidate (§7.2: "up to `w_c`"). If X weighs less than `w_c`, the candidate
  gets X's whole weight, X is left at 0, `candidate.weight` is that weight, and `capped` is true.
- **The rows lead with risk** (§7.2): Δσ_p, Δ diversification ratio, the candidate's share of
  risk (after) and its correlation with the portfolio (before), then ΔSharpe with Memmel's SE,
  then Δ max drawdown. The two candidate rows have no "before". Each is a RiskFigure on the
  panel's metadata, greyed with the diversification ratio below 104 weeks. The SE is
  Jobson–Korkie with Memmel's correction (`measures.sharpe_difference_se`), for two Sharpes
  measured on the same weeks. The two mixes share most of their holdings, so it is far smaller
  than either Sharpe's own SE (0.05 against about 0.6 for a 5% candidate over 3 years). Like
  §5.4's, it is an i.i.d. lower bound.
- **The threshold** SR_c > ρ(c, p)·SR_p is shown under pro-rata funding only. Under replace,
  `applicable` is false, "shown under pro-rata funding only". It is the sign of the mix's Sharpe
  derivative at zero weight, `(σ_c/σ_p)(SR_c − ρ·SR_p)`, which a test checks against a numerical
  derivative. All three inputs are weekly, on the panel's weeks, from excess returns: SR_c is
  the candidate's Sharpe, SR_p is "before"'s, and ρ is computed from the two excess series. ρ is
  not taken from the decomposition, which uses raw returns, because that would make the identity
  approximate. The two differ only through the risk-free rate's weekly variance: 0.3423 against
  0.3424 for EGLN.L on the dev data. The threshold greys with no risk-free series, or below 104
  weeks with the correlation's reason.
- **R14's survivorship flag.** The CHECK found the field: `TickerInfo.status` is None for a ticker
  data-store prices only through a fund's holdings, rather than through `tracked_tickers`
  (data-store's `read_flows.ticker_info`). That ticker is an index constituent the owner never
  tracked. `eligibility.py` names those tickers in `untracked`. A flagged candidate sets
  `candidate.survivorship` and puts `survivorship` on every "after" figure and on
  `candidate_sharpe`, never on a "before" figure. A tracked ticker that is also a constituent
  isn't flagged.
- **Nothing to compare is a 200, not an error** (agreed). If the candidate can't be measured,
  `candidate.status` says why ("no FX series for HKD"). The same applies when no holding is
  included, or when the replaced holding can't be measured (`funding.reason`, "X, the holding
  to replace, can't be measured: …"). Then `window`, `weights`, `rows` and `threshold` are null,
  and no risk-free fetch is made.

The flow logs the start (holdings, exclusions, candidate, weight, funding, window), one line
per asset, the window (and what shortened it), and a summary: Δσ_p, the threshold's outcome or
why it isn't shown, and the survivorship flag. The job logs a start line and a done line, which
is a warning, with the reason, when there is nothing to compare.

**Against the dev data** (2026-10-23; IWDA, IEMA, IUSN, SGLD and VWCE): each request took
0.3–0.4 s and returned about 4 KB. EGLN.L at 10% pro-rata lowered σ_p by 0.2 points, raised the
diversification ratio by 0.09, and cleared the threshold (SR_c above 0.51), with ΔSharpe +0.08
± 0.10. MSFT, which is priced only through IWDA's holdings, was flagged `survivorship`. On Max,
NVCR shortened the window from 2019-08-02 to 2024-01-05.

### Look-through exposures

Where the mix is concentrated, by currency, sector, country and asset class, and by single name
(RK-22; spec §8). `POST /risk/look-through` takes `{assets: [{ticker, weight}]}`, the mix
ex-ante takes, for the same reason: web builds it from the holdings. The weights are
normalised over the assets data-store knows. The response is

    {"assets": [{"ticker", "status", "reason", "kind", "weight", "matched_in", "note"}, ...],
     "exposures": {"snapshots": [{"ticker", "fund_key", "as_of_date", "n_lines",
                                  "raw_weight_sum"}, ...],
                   "coverage": {"look_through", "direct_matched", "direct_unmatched",
                                "commodity", "unmapped"},
                   "dimensions": {"currency" | "trading_currency" | "sector" | "country" |
                                  "asset_class": {"label", "buckets": [{"label", "weight",
                                  "kind"}, ...], "concentration": {...}}},
                   "names": {"top": [{"issuer", "location", "weight", "sources"}, ...],
                             "n_names", "concentration": {...}}}}

with every weight a fixed-point string fraction of the mix, and `concentration` as
`{top_shares: {1, 5, 10}, known_share, herfindahl, effective_n, reason}`.

`look_through/flows.py` classifies each held ticker, in this order:
1. **A commodity**, if it's in `look_through/instruments.py`'s `ETCS` (EGLN.L, SGLD.MI: gold;
   ISLN.L: silver; all priced in USD). Its sector bucket is "Commodity: gold", its asset class
   "Commodity", its country "n/a (commodity)", and its currency the commodity's.
2. **Looked through**, if `GET /etf-holdings` names it as a fund with a snapshot. Today that's
   only IWDA.AS.
3. **Unmapped**, if `tracked_tickers` says `etf` or `etc` (VWCE, EM IMI and Small Cap today).
   It gets one "unmapped: <ticker>" bucket in every dimension, and a `note` says why: "no
   holdings data for this ETF", "holdings never ingested", "no holdings snapshot", "holdings
   unavailable: couldn't reach data-store", or "ETC not in portfolio's instrument table".
4. **A direct stock** otherwise. That includes tickers data-store prices only through a fund's
   holdings, which aren't in `tracked_tickers` at all (BRK-B, PEP). It is matched to a
   looked-through fund's line on that line's `yfinance_ticker` (data-store resolves it, so
   portfolio keeps no copy of the rules). Unmatched (BABA, NVCR), its sector and country are
   "unknown". Its currency is still its own listing currency, and its asset class "Equity".

Agreed rules, and why:
- **ETCs and hedged share classes come from a table in code, not from data-store.**
  `asset_type` is wrong for EGLN.L and ISLN.L (`equity`), and nothing records an ETC's
  underlying currency or a share class's hedge. *Rejected: a YAML config in portfolio*, a new
  config surface for three rows; *and fixing it in data-store*, which needs a schema change.
  `HEDGED` is empty: RK-22's CHECK found no hedged holding. A ticker added there goes into the
  currency view as "hedged: <ticker>" (kind `other`) instead of through its constituents. Its
  other dimensions still look through.
- **An issuer is (normalised name, location)** (`look_through/issuer.py`). Trailing share-class
  words ("CLASS A", "SUBORDINATE VOTING", "PREF", …) are stripped. On IWDA's 2026-08-30
  snapshot this merges exactly the six real multi-line issuers (Alphabet, Atlas Copco, Epiroc,
  Heico, Fox, Lindt). The location keeps apart the two different companies called "EQT" (US and
  Sweden). **Sharp edge:** two different companies with one name in one country would merge.
  None do today. The exact fix is an issuer id from the source. *Rejected: one name per
  listing*, which splits Alphabet, the fifth-biggest name, in two; *and a hand-kept
  share-class map*.
- **Only equity lines are names.** A fund's cash, FX, futures and money-market lines stay in the
  other dimensions under their own asset class, but aren't a company you're exposed to. Two
  ETCs on one commodity are one name ("Commodity: gold"), because that is one exposure.
- **A snapshot is rescaled to 100%.** IWDA's sums to 100.03. The raw sum is returned, and lines
  with no weight are skipped.
- **Every bucket has a kind**: `known`, `unknown`, `unmapped` or `other` (a real exposure that
  isn't one of the dimension's categories). **Herfindahl and effective N are over the known
  share only**, rescaled to 1, with `known_share` beside them. *Rejected: counting an unmapped
  ETF as one bucket*, which would rank VWCE as the biggest single name. With nothing known they
  are null, "no known exposure: every holding is unmapped or unknown". `top_shares` are shares
  of the whole mix.
- **Two currency views** (R16). `currency` is the constituents' listing currency, labelled
  "listing currency of holdings": it's a proxy for exposure, not where revenue comes from.
  `trading_currency` is each held ticker's own listing currency (data-store's `fx_currency`).
- **`names.top` is the 25 heaviest known names**; the concentration and `n_names` cover all of
  them.

Statuses: `included`, `not_tracked` ("not tracked") or `failed` ("couldn't reach data-store",
the exception logged). The remaining weights are renormalised. A failed snapshot fetch makes
that fund unmapped, logged as an error, and the response still answers 200. A failed fund
list or tracked-ticker list fails the request (502), because the classification would be
wrong rather than partial. With nothing included, `exposures` is null.

The flow logs the start (asset count), one line per fund snapshot (date, lines), and one line
per asset (its kind, the fund it matched in, or why it's unmapped or left out). The job logs a
done line with each coverage share, the snapshot and name counts, and how many were left out.

**Against the dev data** (11 holdings, 2026-10-22): 0.17 s and 10 KB. The mix is 70.7% looked
through, 13.1% commodity, 12.8% unmapped and 3.4% direct (1.3% of it matched). The
constituents' currency is 67.8% USD against 5.6% USD by trading currency.

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
