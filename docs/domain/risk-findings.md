# Risk findings

*Moved verbatim from [`tasks-risk-thread.md`](../roadmap-archive/risk.md) (since archived) to
keep it under the repo's 64 KB file limit. These are the facts the risk spec ([`risk.md`](risk.md)) was checked
against: Phase 0's discovery, and the finance review's checks against the code and the dev DB.
Tasks cite them as "Discovery".*

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
