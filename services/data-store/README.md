# data-store

Scrapes iShares ETF holdings, pulls daily OHLCV price history for the constituent tickers, and
stores both in Postgres — orchestrated by Airflow, explorable via pgAdmin.

This README is the operational guide. For how the service is put together and why, see
[`docs/architecture.md`](docs/architecture.md); for the tables themselves,
[`docs/schema.md`](docs/schema.md).

## Fund configuration

Which funds get scraped is driven by `config/etfs.yaml`, which is **gitignored, not committed**.
Set yours up by copying the template:

```bash
cp services/data-store/config/etfs.yaml.example services/data-store/config/etfs.yaml
```

then edit it to list the fund(s) you want to track — see the comments in
`etfs.yaml.example` for the shape of each entry.

**Disclaimer**: data scraped from iShares product pages is subject to iShares' own terms and
conditions. This project provides the tooling only — it is the user's responsibility to review
and comply with those terms (including any restrictions on scraping, redistribution, or
commercial use) for any fund URL they configure here.

## Tracked tickers (ETFs, ETCs, commodities, crypto, standalone stocks)

`config/tracked_tickers.yaml` — also gitignored, also optional — lists tickers to price
*directly*, not derived from any fund's holdings: the ETF itself (not its constituents), an
ETC, crypto, or a standalone stock. Set yours up the same way:

```bash
cp services/data-store/config/tracked_tickers.yaml.example services/data-store/config/tracked_tickers.yaml
```

Each entry needs `ticker` (the yfinance symbol, e.g. `IWDA.AS`, `BTC-USD`), `asset_type` (one
of `equity` / `etf` / `commodity` / `crypto` / `etc`), `display_name`, `price_source`, and
`enabled`. If the file doesn't exist, `ingest-market-data` just treats it as "no tracked
tickers" — you don't need to create it if you only care about fund-holdings-based pricing.

A ticker that appears both here and in a fund's holdings (e.g. a stock you track directly that
also happens to be a holding) is only fetched once per run, not once per source —
`ingest-market-data` resolves the full ticker list across every fund's holdings and this file
before fetching anything, deduping by ticker. If the same ticker appears in both, the
`tracked_tickers.yaml` entry's `asset_type` wins.

## Airflow

Airflow orchestrates ingestion via three DAGs — two scheduled (`ingest_holdings`, `@weekly`;
`daily_pipeline`, `@daily`) and one unscheduled, manually triggered backfill
(`backfill_market_data`).

`daily_pipeline` runs three flows: `ingest-market-data` and `ingest-regimes` start together with
no dependency between them — a hand-curated regimes CSV has nothing to do with fetching prices —
and `calculate-returns` runs afterward, gated on `ingest-market-data`'s `check_run` succeeding
(not merely finishing), since it rebuilds `market_returns` from whatever `ingest-market-data`
just landed. It does not wait on the regimes branch, which writes to an unrelated table.
`ingest-market-data`/`backfill_market_data` fetch prices, dividends and stock splits together in
one pass — a single `Ticker.history()` call already returns all three, so there's no separate
price/dividend DAG pair to keep in sync.

Each DAG calls the same `data_store` job the CLI calls, through `@task.external_python`. That
runs the job in this project's own interpreter — which it must, since the workspace needs Python
3.14 and the Airflow image ships 3.13 — while keeping Airflow's environment free of this
project's heavy dependencies. Crucially it also hands the job's **return value** back to Airflow
as an XCom, so each DAG ends with a `check_run` task that fails the run on a partial failure, or
on a "successful" run that saved nothing. A task reports what happened, not just that a command
exited zero.

What counts as a failure differs by flow, deliberately:

- **A `tickers` value that matches nothing fails.** The run didn't do what it was asked, so it
  goes red rather than quietly green.
- **`ingest_market_data` fails on zero rows.** Its window spans several days, and `rows_saved`
  counts rows in `raw_market_data` — present for every trading day regardless of whether any
  ticker paid a dividend or split that day — so zero means something is wrong even when no
  individual step reported an error. Dividends/splits being sparse (most days, no followed
  ticker has either) doesn't change this: those are columns on a row that's there anyway, not a
  separate save that could legitimately produce nothing.
- **`ingest_regimes` also fails on zero rows saved, but only when the CSV had entries to save.**
  An empty `regimes.csv` is reported as `nothing_to_do`, not a false alarm; a non-empty CSV that
  silently wrote nothing is exactly the anomaly this check exists to catch.
- **`backfill_market_data` does not.** Once every ticker is already covered, a rerun correctly
  saves nothing — that's success, not a stall.

Airflow runs on a **custom image** (`Dockerfile.airflow`: the official `apache/airflow` image +
`uv` + this workspace, baked in at build time, browser included). Bring it up with Docker
Compose — the image builds automatically on first run:

```bash
cd services/data-store
docker compose up -d
```

`dags/` and `config/` are bind-mounted, so DAG edits and configuration changes (a new fund, a
new exchange suffix) take effect on the next run with no rebuild. **Application code under
`src/data_store/` is baked into the image**, so after changing it, rebuild explicitly:

```bash
docker compose build
docker compose up -d --force-recreate airflow-apiserver airflow-scheduler airflow-dag-processor airflow-triggerer
```

Once the stack is healthy, the UI is at [http://localhost:8081](http://localhost:8081) (port
configurable via `AIRFLOW_PORT` in `.env`).

Log in with the admin credentials set in `services/data-store/.env`
(`AIRFLOW_ADMIN_USERNAME` / `AIRFLOW_ADMIN_PASSWORD`, copied from `.env.example` — see that file
for how to generate them). No credentials are stored in this README; check your local `.env`.

`airflow-init` chowns the bind-mounted `dags/`/`logs/` dirs to `AIRFLOW_UID` on every start. Set
`AIRFLOW_UID` in `.env` to your host UID (`id -u`) so those directories stay writable by you — if
you forget and they end up owned by the container's default uid, reclaim them with:

```bash
sudo chown -R $(id -u):$(id -g) services/data-store/dags services/data-store/logs
```

## Database + pgAdmin

Postgres and pgAdmin are also brought up by the same Docker Compose stack:

```bash
cd services/data-store
docker compose up -d postgres pgadmin
uv run data-store db init  # applies src/data_store/db/schema.sql
```

pgAdmin is at [http://localhost:5050](http://localhost:5050) (port configurable via
`PGADMIN_PORT`). Log in with `PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD` from `.env`.

On first run, the bind-mounted `.data/pgadmin` directory may end up owned by `root` (Docker
creates it before pgAdmin's container user can claim it), which makes pgAdmin's container
crash-loop with a `Permission denied: '/var/lib/pgadmin/sessions'` error. If that happens:

```bash
sudo chown -R 5050:5050 services/data-store/.data/pgadmin
docker compose restart pgadmin
```

To explore the data, register a server in pgAdmin:

1. Right-click **Servers** → **Register** → **Server...**
2. **General** tab: any name, e.g. `data-store`
3. **Connection** tab:
   - **Host name/address**: `postgres` — the Compose service name, not `localhost`, since pgAdmin
     reaches Postgres over the Compose network, not your host
   - **Port**: `5432` — the container's internal port, not the host-side `POSTGRES_PORT`
   - **Maintenance database** / **Username** / **Password**: your `POSTGRES_DB` /
     `POSTGRES_USER` / `POSTGRES_PASSWORD` values from `.env`

`etf_holdings` and `raw_market_data` then show up under
`data-store → Databases → <your db> → Schemas → public → Tables`.

## Running ingestion via the CLI

With Postgres up and the schema applied, and `config/etfs.yaml` set up (see above), run the
ingestion flows end to end:

```bash
cd services/data-store
docker compose up -d postgres pgadmin
uv run data-store db init
uv run data-store ingest-holdings              # scrapes iShares, loads etf_holdings
uv run data-store ingest-market-data --days 7  # reads tickers from etf_holdings, loads raw_market_data
uv run data-store calculate-returns            # rebuilds market_returns from raw_market_data
```

`ingest-market-data` (and its `backfill-market-data` counterpart) fetch prices, dividends and
stock splits together — one `Ticker.history()` call per ticker returns all three, so there's no
separate command to run for dividends. They accept `--fund KEY` (only that fund's holdings —
tracked tickers from `tracked_tickers.yaml` are always included regardless), `--days N` (default
7, ingest only) for the lookback window, and `--ticker TICKER` (repeatable) to restrict the run to
specific final, post-suffix-resolution tickers (e.g. `--ticker AAPL --ticker 0700.HK`) — matches
any currently followed ticker, fund-holdings-derived or tracked; useful for a small manual run or
re-attempting specific tickers that previously failed. Omit it to run against everything
followed, as before. A `--ticker` value matching nothing is logged as a warning naming it, so a
typo doesn't quietly look like a successful run that had nothing to do.

### Rerunning a backfill

Run `backfill-market-data` with no `--earliest-date` and it resumes: every ticker that already
has stored data is skipped, and only tickers with none are fetched (as far back as the source
goes). That's what makes a rerun after an interruption cheap — a source returns a ticker's whole
requested range in one call, so each ticker is either fully fetched or untouched, never
half-done, and "has rows" really does mean "finished".

Pass `--earliest-date YYYY-MM-DD` only to reach *further back than a previous run did*: it
fetches each ticker the stretch between that date and its earliest stored date. That mode
re-requests every ticker, so it's the slow path — expect the full multi-hour walk.

### Repairing a ticker after a split

Yahoo restates a ticker's whole price and dividend history into the new scale when it splits, so
the daily ingest re-fetches that ticker's full history automatically whenever it sees a split
(otherwise the rows outside its rolling window would keep the old scale, and the series would read
as a several-hundred-percent one-day move). If that automatic refresh fails, the run logs a warning
naming the ticker — it retries only while the split day stays inside the ingest window, so act on
it rather than waiting:

```bash
uv run data-store backfill-market-data --ticker AAPL --force-refresh
```

`--force-refresh` rewrites the ticker's whole stored range instead of only extending below its
earliest stored date, which neither of the modes above can do. Pair it with `--ticker`; without one
it re-requests every followed ticker's full history.

### Calculating returns

`calculate-returns` rebuilds `market_returns` — split-adjusted price and price/dividend/simple/log
returns — from whatever is currently in `raw_market_data`. Run it after
`ingest-market-data`/`backfill-market-data` land new data:

```bash
uv run data-store calculate-returns                # every ticker with stored market data
uv run data-store calculate-returns --ticker AAPL   # just this ticker (repeatable)
```

Unlike the other flows it reads no config and does no ticker resolution — it discovers sources and
tickers directly from `raw_market_data`. It's also always a **full rebuild** of whatever tickers
it's asked about, never an incremental append: a split changes the correct value of every earlier
row for that ticker, so recomputing everything is what keeps the table correct after one, not just
faster to run. See `docs/schema.md`'s `market_returns` section for the formulas and why there is
no split term in them.

### Loading market regimes (optional)

`config/regimes.csv` — also gitignored, like `etfs.yaml` — is a hand-curated reference list of
historical market and economic regimes (recessions, bear markets, monetary policy cycles, commodity
shocks, geopolitical events), annotated with their scope, dimension, date range, severity and source
citation. Use it to categorize backtests (e.g. "what was the drawdown during the GFC?") and
correlate portfolio behaviour with external conditions.

This file is **optional** — if you don't create it, `ingest-regimes` succeeds and leaves the table
empty. To use it, copy the template and edit:

```bash
cp services/data-store/config/regimes.csv.example services/data-store/config/regimes.csv
```

then edit it to add or modify regime entries — see `regimes.csv.example` for the column definitions
and some sample events. Load it into the database:

```bash
uv run data-store ingest-regimes
```

Each `ingest-regimes` run replaces the entire `regimes` table from the CSV file — full sync, not
upsert. Because the source is hand-curated and changes infrequently, there is no scheduled Airflow
DAG; run it manually after editing the CSV.

### Holdings ingest and market data ingest are independent

`ingest-market-data` always reads the
*current* ticker list from `etf_holdings` (whatever `ingest-holdings` most recently loaded), not
a fresh scrape, so it can run on its own daily cadence without depending on a holdings scrape
having just run.

Non-equity holdings (futures, cash, FX lines) are excluded automatically. Non-US-listed equities
need a yfinance exchange-suffix ticker mapping (e.g. `0700` on the Hong Kong exchange -> `0700.HK`)
— resolved via `config/yfinance_exchange_suffixes.yaml`, a **committed** file (unlike
`etfs.yaml`/`tracked_tickers.yaml`, since it's generic reference data, not personal fund choices).
A holding on an exchange with no entry there is logged as a warning naming the ticker and the raw
exchange string, and skipped for that run — `ingest-market-data`/`backfill-market-data` never
send a bare or guessed ticker to yfinance, which would otherwise silently return zero rows instead
of erroring. If you add a fund with holdings on an exchange not yet in that file, add an entry for
it (query `SELECT DISTINCT exchange FROM etf_holdings` for the exact raw string — don't guess
spelling).

yfinance's per-market ticker syntax also doesn't always match the raw symbol iShares scrapes, even
once the right suffix is appended — e.g. Hong Kong codes need zero-padding (`"1"` → `"0001.HK"`),
Nordic/Canadian share classes use a hyphen where the raw ticker has a space or dot
(`"VOLV B"` → `"VOLV-B.ST"`). These are handled automatically by exchange (see
`pipeline._TICKER_NORMALIZERS`). For one-off cases no general rule covers — the source's exchange
tagging is simply wrong, or a ticker needs a full rewrite (e.g. `"BRKB"` → `"BRK-B"`) — add an
entry to the same file's optional `ticker_overrides:` section; verify it against real yfinance
data before adding it.

## Running ingestion via Airflow

With the full stack up (`docker compose up -d`) and `config/etfs.yaml` set up, the scheduled DAGs
run on their own schedule once unpaused in the UI. To trigger any of them manually — from the
UI's "Trigger DAG" button, or from a shell:

```bash
docker exec <airflow-scheduler-container> airflow dags trigger ingest_holdings
docker exec <airflow-scheduler-container> airflow dags trigger ingest_market_data
docker exec <airflow-scheduler-container> airflow dags trigger calculate_returns
```

Task logs land under `services/data-store/logs/dag_id=.../run_id=.../task_id=.../attempt=N.log`
(bind-mounted, so readable from the host — no need to shell into the container).

`ingest_market_data`/`backfill_market_data`/`calculate_returns` all accept a `tickers` param (a
JSON array, default `[]`) via the UI's "Trigger DAG w/ config" dialog, passed to the job as an
ordinary `list[str]`. Leave it empty to run against everything followed (or, for
`calculate_returns`, everything with stored market data).

There is no shell in the path any more, so the value is never interpolated into a command line
and the escaping the old `BashOperator` needed is simply gone.

Each run's counts are visible in the UI under the task's XCom (`return_value`) — rows saved,
tickers resolved, groups or tickers failed — and are what `check_run` decides on.

## Tests

Unit tests need nothing running and are what a plain `pytest` gives you:

```bash
uv run pytest services/data-store
```

The integration suite is separate, and is the only coverage of the parts a fake can't
reach — `COPY`-into-temp-table upserts, `ON CONFLICT` behaviour, and whether `schema.sql`'s
migrations stay idempotent across reruns. It's marked `integration` and deselected by default
(root `pyproject.toml`), so it only runs when asked for:

```bash
cd services/data-store && docker compose up -d postgres
cd ../.. && uv run pytest services -m integration
```

The tests read `DATABASE_URL` and apply the schema themselves, so an empty database is fine —
no seeding step. CI runs them the same way against a Postgres service container, so they can't
quietly rot into "passes on the machine of whoever started the database".
