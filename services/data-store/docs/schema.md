# data-store schema

DDL lives at `data_store/db/schema.sql` and is applied via `data-store db init`. This document
explains the *why* behind each table's grain and loading strategy; keep it in lockstep with any
schema change (see `.claude/rules/data-schema.md`).

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
| `weight_percent` | `NUMERIC(6,4)`  |                                                                 |
| `notional_value` | `NUMERIC(20,2)` |                                                                 |
| `shares`         | `NUMERIC(20,4)` | CSV "Shares".                                                  |
| `price`          | `NUMERIC(18,6)` |                                                                 |
| `location`       | `VARCHAR(100)`  |                                                                 |
| `exchange`       | `VARCHAR(100)`  |                                                                 |
| `currency`       | `VARCHAR(10)`   |                                                                 |
| `ingested_at`    | `TIMESTAMPTZ`   | Defaults to `now()`.                                           |

Indexes: `(fund_key, as_of_date)` for snapshot lookups, `(holding_ticker)` for cross-fund ticker
lookups (used by `daily_ohlcv` ingestion to find what to price).

No separate `funds` reference table — `fund_key` is a plain string matching the YAML `key`. Easy to
add later if richer per-fund metadata needs to live in the DB.

**Loading strategy: delete-then-insert per `(fund_key, as_of_date)`**, not upsert. A holdings pull
is a full snapshot replacement for that date — there's no meaningful row-level merge within one
snapshot. This is idempotent on rerun and needs no dedup-view machinery: "latest" is just
`MAX(as_of_date)`. History across dates is preserved automatically (each date is its own partition).

## `daily_ohlcv`

One row per `(ticker, price_date, source)` — daily OHLCV price history for tickers found in the
most recent `etf_holdings` snapshot, plus ETFs, ETCs, commodities, and crypto tracked directly
via `config/tracked_tickers.yaml` (not derived from any fund's holdings).

| Column        | Type            | Notes                                             |
|---------------|-----------------|----------------------------------------------------|
| `ticker`      | `VARCHAR(20)`   | Part of PK. For holdings-derived rows, this is the yfinance-ready ticker (raw `etf_holdings.holding_ticker` plus the exchange suffix resolved via `config/yfinance_exchange_suffixes.yaml`, e.g. `0700.HK`), not necessarily equal to `etf_holdings.holding_ticker` itself. Tracked-ticker rows already store the ticker as configured in `tracked_tickers.yaml`. |
| `price_date`  | `DATE`          | Part of PK.                                         |
| `source`      | `VARCHAR(20)`   | Part of PK, e.g. `'yfinance'`.                      |
| `asset_type`  | `VARCHAR(20)`   | `equity` / `etf` / `commodity` / `crypto` / `etc`. `etc` (Exchange Traded Commodity, e.g. a gold-backed ETC) is kept distinct from `commodity` (raw futures contracts, not tracked yet) since they're different instrument wrappers. Not part of the PK — a descriptive attribute of the ticker, not part of its identity. Validated at the app layer (`config_loader.VALID_ASSET_TYPES`), not a DB `CHECK` constraint. |
| `open`        | `NUMERIC(18,4)` |                                                      |
| `high`        | `NUMERIC(18,4)` |                                                      |
| `low`         | `NUMERIC(18,4)` |                                                      |
| `close`       | `NUMERIC(18,4)` |                                                      |
| `adj_close`   | `NUMERIC(18,4)` |                                                      |
| `volume`      | `BIGINT`        |                                                      |
| `ingested_at` | `TIMESTAMPTZ`   | Defaults to `now()`.                                |

Index: `(ticker, price_date)` for time-series lookups. No dedicated index on `asset_type` —
low-cardinality, and the existing index already covers the primary lookup pattern; add one later
if a specific "all rows of type X" query pattern needs it.

**Loading strategy: true upsert** (`INSERT ... ON CONFLICT (ticker, price_date, source) DO UPDATE`).
Successive ingestion runs fetch overlapping date windows, and providers sometimes restate recent
values (late settlement adjustments), so overwriting on rerun is the normal case. `source` is part
of the primary key so a second price source can coexist for the same ticker/date without
collision — the schema-level hook for adding price sources beyond yfinance later.

**Backfill loading pattern**: the daily `ingest-ohlcv` flow above keeps a rolling recent window
filled. Deep history is filled separately by `backfill-ohlcv` (`pipeline.backfill_historical_ohlcv`).
No schema change and no separate checkpoint table — `daily_ohlcv` itself is the checkpoint,
via `get_earliest_price_dates` — but note the table records *how far back rows exist*, not *how
far back we have looked*, so what counts as "already done" depends on the mode
(`pipeline._backfill_window`):

- **No `--earliest-date` (resume, the normal rerun)**: a ticker with any stored row is skipped.
  Sound because a source returns a ticker's whole requested range in one call, so an interrupted
  run leaves each ticker either fully fetched or untouched — never half-done. This is what makes
  resuming cheap.
- **Explicit `--earliest-date`**: fetches the stretch between that date and the ticker's stored
  `MIN(price_date)`, for deliberately reaching further back than a previous run did. Every
  ticker is re-requested, since the table can't say whether the older range was already found
  empty.

**Exchange-suffix resolution for holdings-derived tickers**: `etf_holdings.holding_ticker` is the
raw, unsuffixed symbol as scraped (e.g. `"0700"`). Both `ingest-ohlcv` and `backfill-ohlcv` resolve
it into a yfinance-ready ticker via `pipeline._resolve_yfinance_ticker`, which looks up
`etf_holdings.exchange` in `config/yfinance_exchange_suffixes.yaml` (a raw exchange string -> yfinance
suffix mapping, e.g. `"Hong Kong Exchanges And Clearing Ltd"` -> `.HK`). An exchange with no entry
in that mapping is not guessed at or sent bare to yfinance (which would silently return zero rows,
not an error) — it's logged as a warning naming the ticker and raw exchange string, and that
ticker is skipped for the run. This resolution happens at price-fetch time, not at holdings-scrape
time, so `etf_holdings.holding_ticker` stays raw/unmodified and a mapping-table fix takes effect on
the next pricing run with no re-scrape needed.

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

## `dividend_events`

One row per `(ticker, ex_date, source)` — dividend/distribution events for the same ticker
universe priced into `daily_ohlcv` (fund holdings plus `config/tracked_tickers.yaml` entries).

| Column        | Type            | Notes                                             |
|---------------|-----------------|------------------------------------------------------|
| `ticker`      | `VARCHAR(20)`   | Part of PK. Same resolved, yfinance-ready ticker as `daily_ohlcv.ticker` — see that table's exchange-suffix-resolution notes above; unchanged for `dividend_events`. |
| `ex_date`     | `DATE`          | Part of PK. The ex-dividend date, as returned by yfinance. |
| `source`      | `VARCHAR(20)`   | Part of PK, e.g. `'yfinance'`.                        |
| `asset_type`  | `VARCHAR(20)`   | Same `config_loader.VALID_ASSET_TYPES` values as `daily_ohlcv.asset_type`. |
| `amount`      | `NUMERIC(18,4)` | Per-share cash amount, in the ticker's trading currency (implicit via exchange suffix, same as `daily_ohlcv` — no separate `currency` column). `NOT NULL`: a row only exists for a real, nonzero payment — zero/NaN yfinance rows are filtered out before this table is ever written to. |
| `ingested_at` | `TIMESTAMPTZ`   | Defaults to `now()`.                                  |

Index: `(ticker, ex_date)` for time-series lookups, same rationale as `daily_ohlcv`'s index.

**Loading strategy: true upsert** (`INSERT ... ON CONFLICT (ticker, ex_date, source) DO UPDATE`),
for the same reasons as `daily_ohlcv`: successive `ingest-dividends` runs overlap date windows, and
an occasional late-announced or corrected special dividend needs to overwrite cleanly on rerun
rather than requiring a delete-then-insert cycle.

**Backfill loading pattern**: identical mechanism to `daily_ohlcv`'s, including both resume
modes — `backfill-dividends` (`pipeline.backfill_dividend_events`) computes each ticker's window
via `get_earliest_dividend_dates` and the same `pipeline._backfill_window`; `dividend_events`
itself is the checkpoint, no separate table needed.

**Exchange-suffix resolution and ticker normalization**: identical to `daily_ohlcv` — see that
table's section above. Both flows share the exact same ticker-resolution code
(`pipeline._resolve_priceable_tickers`), so a ticker priced under `daily_ohlcv` resolves to the
same final ticker string under `dividend_events`.
