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

Airflow orchestrates ingestion. Bring it up with Docker Compose:

```bash
cd services/data-store
docker compose up -d
```

Once the stack is healthy, the UI is at [http://localhost:8081](http://localhost:8081) (port
configurable via `AIRFLOW_PORT` in `.env`).

Log in with the admin credentials set in `services/data-store/.env`
(`AIRFLOW_ADMIN_USERNAME` / `AIRFLOW_ADMIN_PASSWORD`, copied from `.env.example` — see that file
for how to generate them). No credentials are stored in this README; check your local `.env`.

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
