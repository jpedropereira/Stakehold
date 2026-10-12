# Full-stack smoke test

Task 13's end-to-end integration: all four services (`auth`, `portfolio`, `data-store`, `web`) and
their three Postgres instances (`auth-postgres`, `app-postgres`, `data-store-postgres`), wired onto
one shared Docker network with in-network service discovery, then a manual walk-through of the
whole flow: register → login → add account → enter/import transactions → rebuild positions/returns
→ view in the web UI.

This is deliberately manual, not a CI step — see walking-skeleton task 13 in
[`roadmap-archive/walking-skeleton.md`](roadmap-archive/walking-skeleton.md) for why.

## How the services find each other

Each service keeps its own independent `docker-compose.yml`, runnable exactly as before
(`cd services/auth && docker compose up`, etc.) — nothing about that changed. Cross-service
reachability is opt-in, via a second compose file per service, `docker-compose.stakehold-net.yml`,
passed alongside the base file with an extra `-f`:

```bash
docker compose -f services/auth/docker-compose.yml       -f services/auth/docker-compose.stakehold-net.yml       up -d
docker compose -f services/data-store/docker-compose.yml -f services/data-store/docker-compose.stakehold-net.yml up -d
docker compose -f services/portfolio/docker-compose.yml  -f services/portfolio/docker-compose.stakehold-net.yml  up -d
docker compose -f services/web/docker-compose.yml         -f services/web/docker-compose.stakehold-net.yml        up -d
```

A single merged root compose file (via `include:`) was tried first and rejected: `auth`,
`portfolio` and `data-store`'s compose files all name their API service `api` — merging all four
into one Compose project silently collapses three services into one, since Compose merges by
service key, not by file. Keeping every service its own separate Compose project, connected only
by a shared external network, avoids that collision entirely and keeps each file fully
independent.

Each overlay file joins only the service that's actually called cross-stack (`api`/`web`, never
`postgres`/`db-init`/`pgadmin`/the Airflow containers) onto `stakehold-net`, and overrides the
caller-side settings that need an in-network address instead of `localhost:<published-port>`:

| Overlay | Joins | Overrides |
|---|---|---|
| `services/web/docker-compose.stakehold-net.yml` | `web` | `WEB_AUTH_API_URL=http://auth-api:8001`, `WEB_PORTFOLIO_API_URL=http://portfolio-api:8002` |
| `services/portfolio/docker-compose.stakehold-net.yml` | `api` | `DATASTORE_API_URL=http://data-store-api:8000` |
| `services/auth/docker-compose.stakehold-net.yml` | `api` | — (auth is a callee only) |
| `services/data-store/docker-compose.stakehold-net.yml` | `api` | — (data-store is a callee only) |

Note the `data-store` port asymmetry: its container's own listen port is `8000`; `8082` is only
where it's published to the host.

## Prerequisites (one-time, per machine)

```bash
docker network create stakehold-net
```

Then, **the user's own step, not something Claude does**: create all four real `.env` files (copy
each service's `.env.example`) and make sure the three shared-secret pairs hold byte-identical
values across the two sides:

| Pair | File A | File B |
|---|---|---|
| web → auth | `services/web/.env` `WEB_INTERNAL_SECRET` | `services/auth/.env` `AUTH_INTERNAL_SECRET` |
| web → portfolio | `services/web/.env` `WEB_PORTFOLIO_INTERNAL_SECRET` | `services/portfolio/.env` `PORTFOLIO_INTERNAL_SECRET` |
| portfolio → data-store | `services/portfolio/.env` `INTERNAL_API_SECRET` | `services/data-store/.env` `INTERNAL_API_SECRET` |

## Bringing the stack up

`./scripts/stack.sh up` runs the four `docker compose ... up -d` commands below in order, then
polls the host-published health checks until all four respond (or times out after ~30s). It's a
thin wrapper — doesn't merge or replace any service's compose file — so the manual sequence below
still works exactly the same and is what to fall back on when something needs diagnosing step by
step.

Run the four `docker compose ... up -d` commands under "How the services find each other" above,
then verify:

```bash
docker compose -f services/auth/docker-compose.yml ps
docker compose -f services/portfolio/docker-compose.yml ps
docker compose -f services/data-store/docker-compose.yml ps
docker compose -f services/web/docker-compose.yml ps
```

Host-published health checks (proves each stack itself is healthy):

```bash
curl -sf http://localhost:8001/health   # auth
curl -sf http://localhost:8002/health   # portfolio
curl -sf http://localhost:8082/health   # data-store
curl -sf http://localhost:8000/health   # web
```

In-network health checks (proves `stakehold-net` itself, not just each stack standalone — reusing
the same `python -c urllib.request` idiom the compose healthchecks already use, since these images
aren't guaranteed to have `curl`):

```bash
docker compose -f services/web/docker-compose.yml exec web \
  python -c "import urllib.request; urllib.request.urlopen('http://auth-api:8001/health', timeout=3)"
docker compose -f services/web/docker-compose.yml exec web \
  python -c "import urllib.request; urllib.request.urlopen('http://portfolio-api:8002/health', timeout=3)"
docker compose -f services/portfolio/docker-compose.yml exec api \
  python -c "import urllib.request; urllib.request.urlopen('http://data-store-api:8000/health', timeout=3)"
```

## The manual flow

In a browser at `http://localhost:8000`:

1. Sign up, then log in.
2. Add an account (`/accounts`).
3. Enter a manual transaction (`/transactions/new?account=<label>`) or import a broker file
   (`/transactions/import?account=<label>`).

**Positions and returns rebuild automatically** when step 3 saves a transaction (see
`services/portfolio/docs/flows.md`, "Positions and returns follow every transaction
write"), and the `portfolio-returns-refresh-all` container rebuilds every user's returns every
six hours to pick up new prices. `portfolio-returns-refresh-new-tickers` rebuilds, every five
minutes, the accounts holding a ticker data-store has just finished loading (a ticker a user added). A ticker-mapping change on the admin page (`/admin/mappings`)
rebuilds the accounts it affects. The manual commands below are only needed for accounts whose
transactions predate those changes, or to refresh returns without waiting for the next run:

```bash
# Look up the signed-up user's id -- there is no lookup-by-email CLI command anywhere.
# Export AUTH_POSTGRES_USER/AUTH_POSTGRES_DB from services/auth/.env into this shell first --
# `exec` runs in the container but the $-expansion below happens in your host shell.
docker compose -f services/auth/docker-compose.yml exec postgres \
  psql -U "$AUTH_POSTGRES_USER" -d "$AUTH_POSTGRES_DB" \
  -c "SELECT id FROM users WHERE email = '<the signup email>';"

docker compose -f services/portfolio/docker-compose.yml exec api \
  uv run --frozen portfolio rebuild-positions --user <uuid>
docker compose -f services/portfolio/docker-compose.yml exec api \
  uv run --frozen portfolio rebuild-returns   --user <uuid>
```

4. Back in the browser: `/accounts/holdings?account=<label>` (via the account's label on
   `/accounts`) to confirm positions and the returns chart render.

## Tearing down

```bash
docker compose -f services/web/docker-compose.yml down
docker compose -f services/portfolio/docker-compose.yml down
docker compose -f services/data-store/docker-compose.yml down
docker compose -f services/auth/docker-compose.yml down
docker network rm stakehold-net   # only if you don't intend to bring the stack back up soon
```
