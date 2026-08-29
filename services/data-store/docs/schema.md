# data-store schema

DDL lives at `src/data_store/db/schema.sql` and is applied via `data-store db init`. This document
explains the *why* behind each table's grain and loading strategy; keep it in lockstep with any
schema change (see `.claude/rules/data-schema.md`). For how the service around these tables is
structured, see [`architecture.md`](architecture.md).

## `etf_holdings`

One row per `(fund_key, as_of_date, holding)` — a full snapshot of an ETF's constituents as scraped
from its provider (iShares, currently) on a given date.

| Column           | Type            | Notes                                                       |
|------------------|-----------------|---------------------------------------------------------------|
| `id`             | `BIGSERIAL`     | Surrogate PK.                                                  |
| `fund_key`       | `VARCHAR(50)`   | Matches the `key` in `config/etfs.yaml`.                       |
| `as_of_date`     | `DATE`          | The scrape date (v1 simplification — not the CSV's own as-of date). |
| `holding_ticker` | `VARCHAR(20)`   | Nullable — cash/derivative lines have none.                    |
| `holding_name`   | `VARCHAR(200)`  |                                                                 |
| `sector`         | `VARCHAR(100)`  |                                                                 |
| `asset_class`    | `VARCHAR(100)`  |                                                                 |
| `market_value`   | `NUMERIC(20,2)` |                                                                 |
| `weight_percent` | `NUMERIC(9,4)`  | Wider than the data needs (weights are <100) on purpose: a snapshot loads as one `COPY`, so a single out-of-range line would abort the whole fund's load, not just that row. |
| `notional_value` | `NUMERIC(20,2)` |                                                                 |
| `shares`         | `NUMERIC(20,4)` | CSV "Shares".                                                  |
| `price`          | `NUMERIC(18,6)` |                                                                 |
| `location`       | `VARCHAR(100)`  |                                                                 |
| `exchange`       | `VARCHAR(100)`  |                                                                 |
| `currency`       | `VARCHAR(10)`   |                                                                 |
| `ingested_at`    | `TIMESTAMPTZ`   | Defaults to `now()`.                                           |

Indexes: `(fund_key, as_of_date)` for snapshot lookups, `(holding_ticker)` for cross-fund ticker
lookups (used by `raw_market_data` ingestion to find what to price).

No separate `funds` reference table — `fund_key` is a plain string matching the YAML `key`. Easy to
add later if richer per-fund metadata needs to live in the DB.

**Migrations**: `schema.sql` is applied by `data-store db init` and must stay safe to rerun on
any database, including one mid-upgrade. Every statement is therefore either `IF NOT EXISTS`-
guarded or wrapped in a `DO $$` block testing the precondition itself — a single failing
statement aborts the entire script, so an unguarded migration doesn't just skip its own step, it
takes the rest of the schema with it. The `dividends` → `dividend_events` rename is the case that
forced this: `ALTER TABLE IF EXISTS ... RENAME` still errors when *both* names exist.

**Loading strategy: delete-then-insert per `(fund_key, as_of_date)`**, not upsert. A holdings pull
is a full snapshot replacement for that date — there's no meaningful row-level merge within one
snapshot. This is idempotent on rerun and needs no dedup-view machinery: "latest" is just
`MAX(as_of_date)`. History across dates is preserved automatically (each date is its own partition).

## `raw_market_data`

One row per `(ticker, price_date, source)` — daily OHLCV, dividends, and stock splits for tickers
found in the most recent `etf_holdings` snapshot, plus ETFs, ETCs, commodities, and crypto tracked
directly via `config/tracked_tickers.yaml` (not derived from any fund's holdings). Replaces the
former separate `daily_ohlcv`/`dividend_events` tables: one row matches one yfinance
`Ticker.history()` response row exactly, dense rather than filtered — `dividends`/`stock_splits`
are `0` on the overwhelming majority of days, not omitted, because this table's job is to be an
honest, unprocessed landing zone for what the source returned. A later phase derives return
calculations from it; this table does not compute anything.

**Migrating from `daily_ohlcv`/`dividend_events`**: `schema.sql` does not drop them — a `DROP
TABLE` for a table an old deploy might still be using doesn't belong permanently baked into the
idempotent schema file the way the guarded `dividends` → `dividend_events` rename above does,
since (unlike a rename) there's no future state where dropping them is still the correct action
to take again. Drop them yourself, once, after confirming `raw_market_data` is populated
(`backfill-market-data`): `DROP TABLE daily_ohlcv; DROP TABLE dividend_events;`.

| Column         | Type             | Notes                                             |
|----------------|------------------|----------------------------------------------------|
| `ticker`       | `VARCHAR(20)`    | Part of PK. For holdings-derived rows, this is the yfinance-ready ticker (raw `etf_holdings.holding_ticker` plus the exchange suffix resolved via `config/yfinance_exchange_suffixes.yaml`, e.g. `0700.HK`), not necessarily equal to `etf_holdings.holding_ticker` itself. Tracked-ticker rows already store the ticker as configured in `tracked_tickers.yaml`. |
| `price_date`   | `DATE`           | Part of PK.                                         |
| `source`       | `VARCHAR(20)`    | Part of PK, e.g. `'yfinance'`.                      |
| `asset_type`   | `VARCHAR(20)`    | `equity` / `etf` / `commodity` / `crypto` / `etc`. `etc` (Exchange Traded Commodity, e.g. a gold-backed ETC) is kept distinct from `commodity` (raw futures contracts, not tracked yet) since they're different instrument wrappers. Not part of the PK — a descriptive attribute of the ticker, not part of its identity. Validated at the app layer (`config_loader.VALID_ASSET_TYPES`), not a DB `CHECK` constraint. |
| `open`         | `NUMERIC(18,4)`  | Split-adjusted, not dividend-adjusted — see below.  |
| `high`         | `NUMERIC(18,4)`  | Split-adjusted, not dividend-adjusted — see below.  |
| `low`          | `NUMERIC(18,4)`  | Split-adjusted, not dividend-adjusted — see below.  |
| `close`        | `NUMERIC(18,4)`  | **Already split-adjusted by the provider** (see "What `close` actually contains"), not dividend-adjusted. The basis for any return calculation this service computes itself — used as-is, with no further split correction. |
| `adj_close`    | `NUMERIC(18,4)`  | yfinance's own blackbox-adjusted close — split *and* dividend adjusted. Stored for reference/validation only; not used for return calculations, since how yfinance derives it isn't documented and has changed defaults across versions before. |
| `volume`       | `BIGINT`         |                                                      |
| `dividends`    | `NUMERIC(18,4)`  | Per-share cash amount, `NOT NULL DEFAULT 0`. `0` on non-event days rather than filtered out — unlike the old `dividend_events` table, this table mirrors the source dataframe's own dense grain. Retroactively split-adjusted on the same basis as `close`, so the two share one scale and can be added directly. |
| `stock_splits` | `NUMERIC(18,6)`  | Split ratio (e.g. `4.0` for a 4-for-1 split, `0.25` for a 1-for-4 reverse split), `NOT NULL DEFAULT 0` on non-split days. Wider precision than the price columns since ratios can be fractional. Read by `pipeline.ingest_market_data` to detect that a ticker's stored history needs restating — see below. |

### What `close` actually contains

**`close` is not the price the stock traded at that day.** Despite the service requesting
`auto_adjust=False`, Yahoo's chart endpoint returns OHLCV already adjusted retroactively for every
split up to the present. Verified against stored data:

| Ticker | Date | Stored `close` | Actually traded | Cumulative later splits |
|---|---|---|---|---|
| AAPL | 2020-08-28 | `124.8075` | $499.23 | ×4 (2020-08-31) |
| AAPL | 2014-06-06 | `23.0561` | $645.57 | ×28 (7:1 in 2014, 4:1 in 2020) |

`dividends` is restated the same way — AAPL's 2014-05-08 dividend is stored as `0.1175` against
the $3.29 actually declared, exactly 3.29/28.

Two consequences, both load-bearing:

1. **Return calculations must not apply a split correction of their own.** `close_t /
   close_(t-1) - 1` is already correct across a split. Dividing the prior close by that day's
   split ratio — the intuitive reading of a "raw" price column, and what an earlier draft of the
   returns design specified — double-adjusts and yields a *+313%* return on AAPL's 2020 split day
   instead of the real +3.39%. Confirmed positively as well as by elimination: computing total
   return this way matches Yahoo's independently-derived `adj_close` series to a maximum of
   0.018% (mean 0.00054%) across 5,441 AAPL rows since 2005, which is `NUMERIC(18,4)` rounding.
2. **A split invalidates every stored row for that ticker.** See the loading note below.
| `ingested_at`  | `TIMESTAMPTZ`    | Defaults to `now()`.                                |

Index: `(ticker, price_date)` for time-series lookups. No dedicated index on `asset_type` —
low-cardinality, and the existing index already covers the primary lookup pattern; add one later
if a specific "all rows of type X" query pattern needs it.

**Loading strategy: true upsert** (`INSERT ... ON CONFLICT (ticker, price_date, source) DO UPDATE`).
Successive ingestion runs fetch overlapping date windows, and providers sometimes restate recent
values (late settlement adjustments, late-announced or corrected dividends), so overwriting on
rerun is the normal case. `source` is part of the primary key so a second market-data provider can
coexist for the same ticker/date without collision — the schema-level hook for adding sources
beyond yfinance later.

**Backfill loading pattern**: the daily `ingest-market-data` flow keeps a rolling recent window
filled. Deep history is filled separately by `backfill-market-data`
(`pipeline.backfill_market_data`). No schema change and no separate checkpoint table —
`raw_market_data` itself is the checkpoint, via `get_earliest_dates` — but note the table records
*how far back rows exist*, not *how far back we have looked*, so what counts as "already done"
depends on the mode (`pipeline._backfill_window`):

- **No `--earliest-date` (resume, the normal rerun)**: a ticker with any stored row is skipped.
  Sound because a source returns a ticker's whole requested range in one call, so an interrupted
  run leaves each ticker either fully fetched or untouched — never half-done. This is what makes
  resuming cheap.
- **Explicit `--earliest-date`**: fetches the stretch between that date and the ticker's stored
  `MIN(price_date)`, for deliberately reaching further back than a previous run did. Every
  ticker is re-requested, since the table can't say whether the older range was already found
  empty.
- **`--force-refresh`**: re-fetches and rewrites a ticker's whole range, ignoring what is already
  stored. Neither mode above can rewrite an existing row — they only ever extend *below* the
  earliest stored date — so this is the repair path for a history left on two scales (below).

**Post-split history restatement.** Because the provider retroactively rescales a ticker's entire
history (see "What `close` actually contains"), and the daily flow only re-fetches a rolling
window, a split would otherwise leave one ticker's rows on two different scales at once — a step
change at the window boundary indistinguishable from a real several-hundred-percent move, which
would silently poison every derived return. So `ingest_market_data` checks each ingested batch for
a non-zero `stock_splits` and re-fetches that ticker's full stored range
(`pipeline._refresh_split_histories`). Splits are rare enough that the cost is negligible.

The sharp edge, stated plainly: this only fires while the split day is inside the rolling ingest
window. If the refresh keeps failing until the split day ages out, nothing re-triggers it and the
ticker stays on two scales with no further warning. The failure is therefore logged as a loud
warning naming `--force-refresh`, which is the recovery path. A durable pending-refresh table was
considered and rejected as disproportionate machinery for an event this rare; it is the fix if
that warning ever proves easy to miss in practice.

**Exchange-suffix resolution for holdings-derived tickers**: `etf_holdings.holding_ticker` is the
raw, unsuffixed symbol as scraped (e.g. `"0700"`). Both `ingest-market-data` and
`backfill-market-data` resolve it into a yfinance-ready ticker via
`pipeline._resolve_yfinance_ticker`, which looks up `etf_holdings.exchange` in
`config/yfinance_exchange_suffixes.yaml` (a raw exchange string -> yfinance suffix mapping, e.g.
`"Hong Kong Exchanges And Clearing Ltd"` -> `.HK`). An exchange with no entry in that mapping is
not guessed at or sent bare to yfinance (which would silently return zero rows, not an error) —
it's logged as a warning naming the ticker and raw exchange string, and that ticker is skipped for
the run. This resolution happens at fetch time, not at holdings-scrape time, so
`etf_holdings.holding_ticker` stays raw/unmodified and a mapping-table fix takes effect on the next
run with no re-scrape needed.

Before appending the suffix, `_resolve_yfinance_ticker` also applies any exchange-specific raw
ticker normalization registered in `pipeline._TICKER_NORMALIZERS` — yfinance's per-market ticker
syntax doesn't always match the raw symbol iShares scrapes (verified against real yfinance data,
not guessed): Hong Kong codes need zero-padding to 4 digits (`"1"` → `"0001.HK"`), London tickers
that already end in `.` must not double it (`"BP."` → `"BP.L"`, not `"BP..L"`), and Nordic/Canadian
share-class tickers need their separator swapped to a hyphen (`"VOLV B"` → `"VOLV-B.ST"`,
`"BBD.B"` → `"BBD-B.TO"`). Separately, `config/yfinance_exchange_suffixes.yaml`'s optional
`ticker_overrides:` section (loaded via `load_ticker_overrides`, same file) holds one-off raw
ticker → final ticker rewrites for cases no general rule covers — either the source's exchange
tagging is simply wrong (e.g. `"HEIA"` tagged under a US exchange but actually needing
`"HEIA.AS"`), or the raw ticker needs a full rewrite (e.g. `"BRKB"` → `"BRK-B"`). An override wins
outright and skips exchange resolution entirely.

## `market_returns`

One row per `(ticker, price_date, source)` — the derived, ready-to-chart counterpart of
`raw_market_data`, rebuilt from it by `pipeline.calculate_returns` (`data-store
calculate-returns`). This is the table a portfolio or chart should read; `raw_market_data` stays
the unprocessed landing zone.

| Column                 | Type             | Notes |
|-------------------------|------------------|-------|
| `ticker`                | `VARCHAR(20)`    | Part of PK. Same value as `raw_market_data.ticker`. |
| `price_date`            | `DATE`           | Part of PK. |
| `source`                | `VARCHAR(20)`    | Part of PK, e.g. `'yfinance'`. |
| `split_adjusted_close`  | `NUMERIC(18,4)`  | Copied verbatim from `raw_market_data.close`, which is already split-adjusted by the provider (see `raw_market_data`, "What `close` actually contains"). This is the chart column. Denormalised on purpose so a chart query needs no join back to `raw_market_data`. |
| `future_split_factor`   | `NUMERIC(30,15)` | Product of every split ratio strictly *after* this date (`1.0` for every date after a ticker's last split). Not otherwise derivable from a single row, and self-verifying against `raw_market_data.stock_splits`. Never legitimately `0` — see "Why the scale is 15" below. |
| `price_return`          | `NUMERIC(18,8)`  | `close_t / close_(t-1) - 1`. Nullable — `NULL` on a ticker's first stored date, where no prior close exists, or when the row is flagged as an implausible single-day return — see "Implausible single-day returns are suppressed, not corrected" below. **No split term** — see below. |
| `dividend_return`       | `NUMERIC(18,8)`  | `dividends_t / close_(t-1)`. `0` (not `NULL`) on a non-dividend day; `NULL` on the series-anchor row, or when the row is flagged as an implausible single-day return — see below. |
| `simple_return`         | `NUMERIC(18,8)`  | `(close_t + dividends_t) / close_(t-1) - 1`. Always equals `price_return + dividend_return` exactly when both are non-null — kept as its own column for query convenience, computed in the same pass so the two can never drift apart. This is the number to display directly (`"+2.3% today"`). Nullable — same two cases as `price_return`. |
| `log_return`            | `NUMERIC(18,8)`  | `ln((close_t + dividends_t) / close_(t-1))`, equivalently `ln(1 + simple_return)`. What gets summed across days for a cumulative/charted return, since log returns compound additively and simple returns don't. Nullable — same two cases as `price_return`. |
| `calculated_at`         | `TIMESTAMPTZ`    | Defaults to `now()`; also bumped on every upsert, so it reflects the last rebuild, not the first. |

Index: `(ticker, price_date)`, same rationale as `raw_market_data`'s.

**Why `future_split_factor`'s scale is 15.** Cumulative *reverse* splits drive this column
towards zero, and the original `NUMERIC(18,6)` bottomed out at `1e-6`. `BMPS.MI` has four reverse
splits (`0.01 x 0.05 x 0.01 x 0.01`) that multiply out to `5e-8`, so 3,814 of its rows stored
`0` — which reads as a valid factor rather than a missing one, and silently zeroed the
`split_adjusted_close x future_split_factor` derivation below. Measured across the real 1,278-ticker
dataset the column has to span `5e-8` to `1e4`; `NUMERIC(30,15)` covers `1e-15` to `1e15`, roughly
seven decimal orders of headroom below today's worst case. Fifteen fractional digits is also as
much as the `float64` the factor is computed in can distinguish, so a wider scale would store
false precision rather than more information.

No fixed scale is underflow-proof, so the scale is paired with a guard:
`returns/repository.py`'s `_NUMERIC_MIN_MAGNITUDE` warns about and skips a row whose factor would
still round to zero, rather than storing an indistinguishable `0`. That lower bound applies only
to this column — every other numeric column here is a return, for which exactly `0` is a flat day
rather than a defect.

**No stored as-traded price column, deliberately.** An earlier design also stored
`as_traded_close` — the genuinely raw, historically-traded price (`split_adjusted_close ×
future_split_factor`), for reconciling against brokerage statements and filings, which quote
prices as actually traded. Dropped before merge: nothing in this service reads it (not the
return formulas, not the charting use case that motivated `split_adjusted_close`), it's a
one-multiplication derivation from two columns already stored, and its only justification was a
brokerage-transaction-reconciliation feature `portfolio` doesn't have yet. If that need arises,
compute it in the query that needs it — `split_adjusted_close * future_split_factor` — rather
than paying to store and maintain a column with no current reader. It also carried a correctness
caveat worth avoiding: it would only be right if every split between a date and today were
present in `raw_market_data`, which is exactly what the post-split history restatement
(`raw_market_data`, "Post-split history restatement") exists to guarantee, but is a sharper edge
than a table with no consumers should carry.

**Why there is no split term in the return formulas.** The obvious-looking design — divide the
prior day's close by that day's split ratio before comparing — assumes `close` is the as-traded
price. It isn't (see `raw_market_data`, "What `close` actually contains"): the provider already
retroactively split-adjusts `close`, so that correction double-adjusts and produces a
triple-digit fake return on every split day. Confirmed against real AAPL data before this
shipped: the corrected formula above matches Yahoo's independently-derived `adj_close` series to
a maximum 0.018% (mean 0.00054%) over 5,441 rows spanning two splits and 21 years — `NUMERIC(18,4)`
rounding, not coincidence.

**Implausible single-day returns are suppressed, not corrected.** A 460-row / 45-ticker
investigation against the real dataset found `abs(simple_return) > 100%` in a single day, and
root-caused all of them:

- **428 rows (~35 tickers)**: the return spans a day where `raw_market_data.volume = 0`, either
  on the return's own date or the immediately preceding one. On these no-trade days (mostly
  exchange holidays), the provider's `close` is a garbage value at a roughly-constant-but-wrong
  per-ticker scale rather than a real quote — e.g. `ULVR.L` prints ~52x on 51 of its 52 such
  rows, all on UK bank holidays with `volume = 0`.
- **15 rows (6 tickers)**: `dividends` alone implies a >100% one-day yield while the price barely
  moved — a dividend-field magnitude glitch, unrelated to volume.
- **17 rows (15 tickers)**: no mechanical signature (real volume both days, no dividend
  anomaly) — a genuine mix of likely-real moves (e.g. a biotech catalyst day) and likely-still-
  defective ones. These are **deliberately left untouched**: a reader querying `market_returns`
  for extreme returns should know some known-suspect rows remain by design, not by oversight.

`returns/calculator.py`'s `compute_returns` nulls `price_return`, `dividend_return`,
`simple_return`, and `log_return` together (never just one, to keep the
`simple_return == price_return + dividend_return` invariant intact) whenever a row's
`simple_return` exceeds `_IMPLAUSIBLE_RETURN_THRESHOLD` (100%) **and** either its own or the
prior day's `raw_market_data.volume` is exactly `0`, or `dividend_return` alone exceeds the same
threshold. A `NULL` volume is not treated as zero — the signature confirmed by the investigation
is specifically `volume = 0`, not "volume missing."

Two things this does **not** do, worth stating plainly:

- It never touches `raw_market_data` or `split_adjusted_close`. A price chart built from
  `split_adjusted_close` will still show the garbage spike on these dates (e.g. `ULVR.L`'s ~52x
  print) even though the *return* computed across it is now suppressed — this fix addresses
  returns, not the underlying price.
- It has a false-positive risk it doesn't try to eliminate: a genuine trading halt and
  reopening (zero volume on the halt day, a real large move the next day) would also be nulled.
  The 93% concentration found in the investigation is why this trade-off was accepted rather
  than building a more targeted (and more complex) halt-vs-defect detector.

**Loading strategy: full rebuild, not incremental append or upsert-on-new-rows-only.**
`calculate_returns` recomputes and upserts every row for the tickers it's asked about, every
run. A split retroactively changes the correct value of `future_split_factor` for every earlier
date of that ticker — an incremental design that only appended new dates would serve stale, wrong
values for the rest of the series after a split, which is worse than recomputing everything on a
cadence generous enough to be cheap (the full 8.95M-row table rebuilds in minutes, not hours).

**Known limitation: upsert never deletes.** The loader only ever inserts or updates, so a row
that stops being produced is left behind at whatever value it last held. Three paths reach that
state, not one:

- A row is removed from `raw_market_data` (not a normal occurrence today).
- `compute_returns` drops a row whose `close` is missing or non-positive.
- `save_market_returns` skips a row whose computed value is out of range for its column.

The last two are live on real data — a full rebuild of the dev database drops tens of rows per
batch for non-positive/missing `close`. In all three cases "full rebuild self-heals" holds for
changed and added rows, but not for rows that disappear from the output: a previously written
`market_returns` row for that `(ticker, price_date, source)` survives the rebuild untouched, and
its `calculated_at` still advances only when it *is* rewritten, so staleness isn't visible from
the row itself. Cleaning this up needs a delete pass keyed on what the rebuild actually wrote —
not implemented here.

**Batched by ticker, not one table-wide query.** `raw_market_data` is 8.95M rows across 1,278
tickers, and this can run inside an Airflow worker. Returns are purely per-ticker (see
`returns/calculator.py`), so `pipeline.calculate_returns` batches a few hundred tickers at a time
(`returns/repository.py`'s `iter_price_series`, `RETURNS_BATCH_SIZE = 250`) and isolates a batch's
save failure from the batches around it, the same failure-isolation pattern `raw_market_data` uses
per ticker. Measured on a full unfiltered rebuild of the real dev data: ~3.5GB peak RSS, 3m28s —
bounded and well short of what one unbatched DataFrame over the whole table would need, but
notably more than a naive per-column `float64` estimate suggests, since `save_market_returns`'s
row-by-row `COPY` staging (mirroring `save_raw_market_data`'s) allocates a Python object per cell
per row. Acceptable for an on-demand/scheduled rebuild; revisit the batch size or the `COPY`
staging loop if this needs to run somewhere more memory-constrained than a modern worker.

**No ticker resolution or config coupling.** Unlike the pricing flows, `calculate_returns`
doesn't read `etf_holdings` or any config file — it operates on exactly what's already stored in
`raw_market_data`, discovering both sources (`SELECT DISTINCT source`) and tickers
(`SELECT DISTINCT ticker`) from the data itself. This also means it keeps working for a source
that has since been retired but still has stored rows.

**Precision inherited from `raw_market_data`, not improved on.** `raw_market_data` stores prices
as `NUMERIC(18,4)`, which represents a sub-cent price like `PLS.AX`'s `0.0093` with only two
significant figures. `split_adjusted_close` copies that value directly, so a return computed for
such a ticker inherits the same quantisation. Out of scope to fix here since it originates in the
upstream table.

## `fx_rates`

One row per `(base_currency, quote_currency, rate_date, source)` — a daily currency-conversion
rate fetched from yfinance, for every currency configured in `config/currencies.yaml` against
that file's `base_currency`. A separate table from `raw_market_data`, deliberately: an FX rate
has no OHLC, dividends, or splits, and forcing that shape onto it would mix currency-conversion
lookups with `raw_market_data`'s unrelated split-restatement machinery for no benefit — nothing
here needs a price chart, just a single daily number to convert with.

| Column           | Type            | Notes                                                     |
|------------------|-----------------|--------------------------------------------------------------|
| `base_currency`  | `VARCHAR(3)`    | Part of PK. The `base_currency` from `config/currencies.yaml` (e.g. `EUR`) at the time this row was fetched. |
| `quote_currency` | `VARCHAR(3)`    | Part of PK. Any other enabled currency from that file (e.g. `USD`).  |
| `rate_date`      | `DATE`          | Part of PK.                                                   |
| `source`         | `VARCHAR(20)`   | Part of PK, e.g. `'yfinance'`. Same rationale as `raw_market_data.source` — a second FX provider can coexist for the same pair/date without collision. |
| `rate`           | `NUMERIC(18,8)` | Units of `quote_currency` per 1 unit of `base_currency` — standard FX quoting convention, and exactly what the source ticker returns: pair `(EUR, USD)` fetches yfinance ticker `EURUSD=X`, whose `Close` is USD per 1 EUR, stored here verbatim as `rate`. To convert an amount from `quote_currency` to `base_currency`, divide by `rate`; the other direction, multiply. |
| `ingested_at`    | `TIMESTAMPTZ`   | Defaults to `now()`.                                          |

Index: `(rate_date)` for "give me every pair's rate on date X" lookups — the primary key already
covers "give me this pair's history".

**No cross-rates stored.** A rate between two non-base currencies (e.g. GBP → CHF) is not fetched
or stored directly — it's derived downstream by division through the shared base
(`rate(GBP, CHF) = rate(EUR, CHF) / rate(EUR, GBP)` when `EUR` is the base). This keeps the
number of yfinance tickers fetched at `len(currencies) - 1` rather than every directed pair, and
avoids the sparser data some exotic direct crosses have on yfinance.

**Loading strategy: true upsert** (`INSERT ... ON CONFLICT (base_currency, quote_currency,
rate_date, source) DO UPDATE`), same rationale as `raw_market_data`: successive ingestion runs
fetch overlapping date windows, and a provider may restate a recent rate.

**Backfill loading pattern**: identical in shape to `raw_market_data`'s — `data-store
backfill-fx-rates` fills the stretch of history each pair is still missing, using `fx_rates`
itself as the checkpoint (`get_earliest_fx_dates`, the FX equivalent of `get_earliest_dates`). The
same three modes apply (resume / reach further back via `--earliest-date` / `--force-refresh`),
for the same reasons documented under `raw_market_data`'s "Backfill loading pattern" above — FX
rates have no split-restatement concern, so `--force-refresh` exists here only for symmetry and
general repair, not a specific known failure mode the way it does for `raw_market_data`.

## `regimes`

One row per unique regime identifier — a historical market/economic event or condition (recession,
bear market, monetary policy cycle, commodity shock, geopolitical event) annotated with its scope
(US equity, global, Euro area, etc.), dimension (business cycle, drawdown, inflation, etc.), date
range, severity, and source citation. A reference table for backtesting and analysis queries like
"what was the drawdown during the GFC?".

| Column        | Type            | Notes                                                       |
|---------------|-----------------|---------------------------------------------------------------|
| `regime_id`   | `VARCHAR(50)`   | Natural key, e.g. `'gfc'`, `'us_rec_2007'`, `'spx_bear_1980'`. |
| `dimension`   | `VARCHAR(30)`   | Category: `'business_cycle'`, `'drawdown'`, `'inflation'`, etc. |
| `scope`       | `VARCHAR(20)`   | Geographic or market scope: `'US'`, `'Global'`, `'EA'`, `'SPX'`, etc. |
| `name`        | `VARCHAR(200)`  | Human-readable title, e.g. `'Great Recession'` or `'COVID recession'`. |
| `start_date`  | `DATE`          | Regime start, required.                                      |
| `end_date`    | `DATE`          | Regime end, nullable — ongoing regimes have no end date.    |
| `known_from`  | `DATE`          | Date this regime was identified/announced, nullable — some historical regimes are defined only in retrospect. |
| `severity`    | `VARCHAR(20)`   | Scalar severity: `'mild'`, `'moderate'`, `'severe'`, `'extreme'`, nullable. Validated at app layer, not a DB `CHECK`. |
| `source`      | `VARCHAR(50)`   | Citation/authority: `'NBER'`, `'CEPR-EABCDC'`, `'manual_approx'`, etc., required. |
| `notes`       | `TEXT`          | Free-form annotations, nullable.                             |
| `ingested_at` | `TIMESTAMPTZ`   | Defaults to `now()`.                                         |

Indexes: `(dimension, scope)` for queries like "all EA recessions", `(start_date)` for date-range
joins against price data.

No surrogate key — `regime_id` is the natural key and stable across reruns.

**Loading strategy: full sync (DELETE then COPY), not upsert.** The source is a hand-curated
CSV (`config/regimes.csv`, gitignored). Each `ingest-regimes` run replaces the entire table from
the current CSV. Unlike append-only event tables like `raw_market_data`, regime definitions can
be edited, split, merged, or removed as curation improves — the DB should mirror the file exactly,
not accumulate stale rows. Full-sync design is simple, idempotent, and enforces the invariant:
"the DB always matches the source file".

**Ingestion**: runs daily via the `daily_pipeline` DAG's `ingest_regimes` task, alongside market
data ingest. Since the CSV is hand-curated and rarely changes, most days this just replaces the
table with the same rows; it can still be triggered manually via `data-store ingest-regimes` right
after editing the CSV, without waiting for the next scheduled run.
