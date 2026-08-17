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
filled. Deep history is filled separately by `backfill-ohlcv` (`pipeline.backfill_historical_ohlcv`),
which for each ticker computes `MIN(price_date)` already stored (`get_earliest_price_dates`) and
fetches only the stretch older than that — never re-requesting a range already covered. No
schema change, no separate checkpoint table: `daily_ohlcv` itself is the checkpoint, since the gap
is recomputed fresh from committed rows on every run, making a partial/interrupted backfill cheap
to resume rather than needing to restart from scratch.

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
