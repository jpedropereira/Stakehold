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
`enabled`. If the file doesn't exist, `ingest-ohlcv` just treats it as "no tracked tickers" —
you don't need to create it if you only care about fund-holdings-based pricing.

A ticker that appears both here and in a fund's holdings (e.g. a stock you track directly that
also happens to be a holding) is only fetched once per run, not once per source — `ingest-ohlcv`
resolves the full ticker list across every fund's holdings and this file before fetching
anything, deduping by ticker. If the same ticker appears in both, the `tracked_tickers.yaml`
entry's `asset_type` wins.

## Airflow

Airflow orchestrates ingestion via five DAGs — three scheduled (`ingest_holdings`, `@weekly`;
`ingest_daily_ohlcv` and `ingest_dividends`, both `@daily`) and two unscheduled, manually
triggered backfills (`backfill_historical_ohlcv`, `backfill_dividend_events`) — each a thin
wrapper shelling out to the same `data-store` CLI already covered above —
see `Dockerfile.airflow` for why (keeps Airflow's own Python environment free of this project's
heavy deps, and means what runs in the DAG is byte-for-byte what you can run and test locally).

Airflow runs on a **custom image** (`Dockerfile.airflow`: the official `apache/airflow` image +
`uv` + this workspace, baked in at build time, browser included). Bring it up with Docker
Compose — the image builds automatically on first run:

```bash
cd services/data-store
docker compose up -d
```

**After changing `data_store` code**, rebuild explicitly — unlike `dags/` (bind-mounted, picked
up live), the application code baked into the image is a build-time snapshot:

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
uv run data-store db init  # applies data_store/db/schema.sql
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

`etf_holdings`, `daily_ohlcv`, and `dividend_events` then show up under
`data-store → Databases → <your db> → Schemas → public → Tables`.

## Running ingestion via the CLI

With Postgres up and the schema applied, and `config/etfs.yaml` set up (see above), run both
ingestion flows end to end:

```bash
cd services/data-store
docker compose up -d postgres pgadmin
uv run data-store db init
uv run data-store ingest-holdings          # scrapes iShares, loads etf_holdings
uv run data-store ingest-ohlcv --days 7    # reads tickers from etf_holdings, loads daily_ohlcv
uv run data-store ingest-dividends --days 7  # same ticker universe, loads dividend_events
```

`ingest-ohlcv` and `ingest-dividends` (and their `backfill-ohlcv`/`backfill-dividends`
counterparts) all accept `--fund KEY` (only that fund's holdings — tracked tickers from
`tracked_tickers.yaml` are always included regardless), `--days N` (default 7, ingest commands
only) for the lookback window, and `--ticker TICKER` (repeatable) to restrict the run to specific
final, post-suffix-resolution tickers (e.g. `--ticker AAPL --ticker 0700.HK`) — matches any
currently followed ticker, fund-holdings-derived or tracked; useful for a small manual run or
re-attempting specific tickers that previously failed. Omit it to run against everything
followed, as before. A `--ticker` value matching nothing is logged as a warning naming it, so a
typo doesn't quietly look like a successful run that had nothing to do.

### Rerunning a backfill

Run `backfill-ohlcv` / `backfill-dividends` with no `--earliest-date` and they resume: every
ticker that already has stored data is skipped, and only tickers with none are fetched (as far
back as the source goes). That's what makes a rerun after an interruption cheap — a source
returns a ticker's whole requested range in one call, so each ticker is either fully fetched or
untouched, never half-done, and "has rows" really does mean "finished".

Pass `--earliest-date YYYY-MM-DD` only to reach *further back than a previous run did*: it
fetches each ticker the stretch between that date and its earliest stored date. That mode
re-requests every ticker, so it's the slow path — expect the full multi-hour walk.

All three flows are independent — `ingest-ohlcv` and `ingest-dividends` always read the *current*
ticker list from `etf_holdings` (whatever `ingest-holdings` most recently loaded), not a fresh
scrape, so each can run on its own daily cadence without depending on a holdings scrape having
just run.

Non-equity holdings (futures, cash, FX lines) are excluded automatically. Non-US-listed equities
need a yfinance exchange-suffix ticker mapping (e.g. `0700` on the Hong Kong exchange -> `0700.HK`)
— resolved via `config/yfinance_exchange_suffixes.yaml`, a **committed** file (unlike
`etfs.yaml`/`tracked_tickers.yaml`, since it's generic reference data, not personal fund choices).
A holding on an exchange with no entry there is logged as a warning naming the ticker and the raw
exchange string, and skipped for that run — `ingest-ohlcv`/`backfill-ohlcv` never send a bare or
guessed ticker to yfinance, which would otherwise silently return zero rows instead of erroring.
If you add a fund with holdings on an exchange not yet in that file, add an entry for it (query
`SELECT DISTINCT exchange FROM etf_holdings` for the exact raw string — don't guess spelling).
`ingest-dividends`/`backfill-dividends` reuse this exact same resolution — a ticker priced under
`daily_ohlcv` resolves identically for `dividend_events`.

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
docker exec <airflow-scheduler-container> airflow dags trigger ingest_daily_ohlcv
docker exec <airflow-scheduler-container> airflow dags trigger ingest_dividends
```

Task logs land under `services/data-store/logs/dag_id=.../run_id=.../task_id=.../attempt=N.log`
(bind-mounted, so readable from the host — no need to shell into the container).

`ingest_daily_ohlcv`/`backfill_historical_ohlcv` and `ingest_dividends`/`backfill_dividend_events`
all accept a `tickers` param (a JSON array, default `[]`) via the UI's "Trigger DAG w/ config"
dialog, mapped straight to the CLI's `--ticker` flag (each list value shell-escaped before being
interpolated into the task's `bash_command`, so a malicious/malformed value can't break out of its
argument). Leave it empty to run against everything followed, as before.

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
