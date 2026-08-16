# data-store

Scrapes iShares ETF holdings, pulls daily OHLCV price history for the constituent tickers, and
stores both in Postgres — orchestrated by Airflow, explorable via pgAdmin.

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

## Airflow

Airflow orchestrates ingestion via two DAGs (`ingest_holdings`, `@weekly`; `ingest_daily_ohlcv`,
`@daily`), each a thin wrapper shelling out to the same `data-store` CLI already covered above —
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

`etf_holdings` and `daily_ohlcv` then show up under
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
```

Both subcommands accept `--fund KEY` to target a single fund (default: all funds with
`enabled: true` in `etfs.yaml`). `ingest-ohlcv` also accepts `--days N` (default 7) for the
lookback window.

The two flows are independent — `ingest-ohlcv` always reads the *current* ticker list from
`etf_holdings` (whatever `ingest-holdings` most recently loaded), not a fresh scrape, so it can
run on its own daily cadence without depending on a holdings scrape having just run.

Non-equity holdings (futures, cash, FX lines) are excluded automatically. Non-US-listed equities
that need an exchange-suffix ticker mapping for yfinance (e.g. Tokyo, Stockholm) aren't resolved
yet — `ingest-ohlcv` will skip them and log a "possibly delisted" warning from yfinance rather
than failing the whole run.

## Running ingestion via Airflow

With the full stack up (`docker compose up -d`) and `config/etfs.yaml` set up, the two DAGs run
on their own schedule once unpaused in the UI. To trigger either one manually — from the UI's
"Trigger DAG" button, or from a shell:

```bash
docker exec <airflow-scheduler-container> airflow dags trigger ingest_holdings
docker exec <airflow-scheduler-container> airflow dags trigger ingest_daily_ohlcv
```

Task logs land under `services/data-store/logs/dag_id=.../run_id=.../task_id=.../attempt=N.log`
(bind-mounted, so readable from the host — no need to shell into the container).
