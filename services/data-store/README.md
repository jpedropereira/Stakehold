# data-store

Scrapes iShares ETF holdings, pulls daily OHLCV price history for the constituent tickers, and
stores both in Postgres — orchestrated by Airflow, explorable via pgAdmin.

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
