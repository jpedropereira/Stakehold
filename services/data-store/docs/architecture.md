# data-store architecture

How this service is put together and why. For *what the tables look like* see
[`schema.md`](schema.md); for *how to run it* see [`../README.md`](../README.md). This document
covers the parts neither of those does: the layering, the boundaries between modules, and the
decisions that would otherwise look arbitrary.

## What the service is responsible for

Getting external financial data into Postgres, correctly and repeatably. Three things go in:

- **ETF holdings** — scraped from a fund provider's website (iShares today).
- **Daily OHLCV prices** — for every ticker those holdings contain, plus directly tracked ones.
- **Dividend events** — for that same ticker universe.

It does *not* calculate returns, value portfolios, or decide what's worth owning. Those belong to
`portfolio`, which reads these tables. The dividing line is deliberate: this service's only job is
that the data in Postgres is a faithful, complete record of what the sources said.

## Layering

Dependencies point one way, downward. Nothing in a lower layer imports from a higher one.

```mermaid
flowchart TD
    subgraph entry["Entry points"]
        CLI["cli.py<br/><i>argparse, one handler per command</i>"]
        DAGS["dags/*.py<br/><i>Airflow, shells out to the CLI</i>"]
    end

    subgraph orch["Orchestration"]
        PIPE["pipeline.py<br/><i>the five flows; ticker resolution</i>"]
    end

    subgraph domain["Domain modules"]
        HOLD["holdings/<br/>scraper · parser · repository"]
        PRICE["prices/<br/>yfinance_source · repository"]
        DIV["dividends/<br/>yfinance_source · repository"]
    end

    subgraph found["Foundation"]
        CONF["config_loader.py"]
        SET["settings.py"]
        DB["db/connection.py + schema.sql"]
    end

    PG[("Postgres")]
    EXT(["External: iShares, yfinance"])

    DAGS -->|subprocess| CLI
    CLI --> PIPE
    CLI --> CONF
    CLI --> SET
    CLI --> DB
    PIPE --> HOLD
    PIPE --> PRICE
    PIPE --> DIV
    HOLD --> EXT
    PRICE --> EXT
    DIV --> EXT
    HOLD --> PG
    PRICE --> PG
    DIV --> PG
    DB --> PG
```

The rule that keeps this honest: **`pipeline.py` never touches SQL or HTTP directly, and the
domain modules never decide *what* to fetch.** Orchestration knows which tickers matter and in
what order; the domain modules know how to talk to one source or one table. When those blur, the
next data source becomes a rewrite instead of a new file.

## The three-file shape of a domain module

Each domain follows the same split, and a new one should too:

| File | Owns | Never does |
|---|---|---|
| `base.py` | The `Protocol` a source must satisfy | Anything concrete |
| `*_source.py` | Talking to one external provider; reshaping its response into long-form rows | Touch the database |
| `repository.py` | Reading and writing one table | Know where the data came from |

`holdings/` has an extra pair because scraping is two steps: `scraper.py` drives a real browser to
resolve the download URL, and `parser.py` fetches and parses the CSV. They're split because only
the first needs Playwright — the resolved URL is a plain unauthenticated GET, so the expensive
browser dependency stays confined to the one function that genuinely needs it.

`column_mapping.py` is the single declaration of how provider CSV columns map to database columns
and types. A provider renaming a column is a one-line change there, not a hunt through parsing
code.

## Extension points

New providers are registry entries, not new branches. All three registries live at the top of
`pipeline.py`:

```python
SCRAPERS         = {"ishares": get_holdings_csv_url}
PRICE_SOURCES    = {"yfinance": YFinancePriceSource}
DIVIDEND_SOURCES = {"yfinance": YFinanceDividendSource}
```

Adding a second price provider means writing a class satisfying `prices/base.py`'s `PriceSource`
and adding one line. No flow function changes.

`PRICE_SOURCES` and `DIVIDEND_SOURCES` are kept separate even though yfinance is currently the only
entry in both, because a price source and a dividend source are different Protocols with different
`fetch()` shapes. Merging them would mean pulling a class out of one registry and hoping it also
happens to expose the other's surface.

**One sharp edge if you add a source:** the registry *key* and the class's `.name` attribute are
two different strings. `.name` is what gets stamped into the `source` column and what backfill
floors are read under; the key is what config files reference. Keep them equal unless you have a
reason not to.

## The five flows

All five live in `pipeline.py` and are exposed as CLI subcommands.

| Flow | Command | Cadence | Reads | Writes |
|---|---|---|---|---|
| Holdings ingest | `ingest-holdings` | weekly | provider site | `etf_holdings` |
| Price ingest | `ingest-ohlcv` | daily | `etf_holdings` + config | `daily_ohlcv` |
| Dividend ingest | `ingest-dividends` | daily | `etf_holdings` + config | `dividend_events` |
| Price backfill | `backfill-ohlcv` | manual | `daily_ohlcv` | `daily_ohlcv` |
| Dividend backfill | `backfill-dividends` | manual | `dividend_events` | `dividend_events` |

**They are deliberately independent.** The pricing flows read whatever ticker list is currently in
`etf_holdings`; they never trigger a scrape. So a holdings scrape failing doesn't cascade into
prices going stale, and each flow can run on its own schedule. The coupling is through the table,
not through the call graph.

**Daily and backfill are separate commands, not one command with a flag**, because they have
opposite performance shapes. The daily flows batch many tickers per request and are expected to
finish in seconds; the backfills fetch one ticker at a time with a deliberate sleep between them
and can run for hours. Merging them would mean one set of rate-limit and batching decisions
serving two incompatible needs.

### Ticker resolution, the load-bearing part

Everything that prices anything goes through the same resolution step, and it's where most of the
service's accumulated knowledge lives:

```mermaid
flowchart LR
    A["etf_holdings<br/>raw ticker + exchange"] --> B{"ticker_overrides<br/>has an entry?"}
    B -->|yes| Z["final ticker"]
    B -->|no| C{"exchange in<br/>suffix mapping?"}
    C -->|no| D["warn + skip<br/><i>never guess</i>"]
    C -->|yes| E["apply per-exchange<br/>normalizer"]
    E --> F["append suffix"]
    F --> Z
    G["tracked_tickers.yaml<br/><i>already provider-ready</i>"] --> Z
```

Three things about this are intentional:

1. **An unmapped exchange is skipped with a warning, never guessed.** The provider returns *no
   rows* rather than an error for a bad symbol, so a guessed ticker would look like a ticker that
   simply had no trading activity. Refusing to guess is what keeps "no data" meaningful.
2. **Resolution happens at fetch time, not at scrape time.** `etf_holdings.holding_ticker` stays
   exactly as scraped, so fixing a mapping takes effect on the next pricing run with no re-scrape.
3. **Tracked tickers bypass it entirely.** They're configured in provider-ready form already, so
   putting them through exchange resolution would corrupt them.

The per-exchange normalizers (`_TICKER_NORMALIZERS`) encode verified quirks — Hong Kong codes need
zero-padding, London tickers ending in `.` must not double it, Nordic and Toronto share classes use
a hyphen. These were confirmed against real provider responses, not inferred. Treat them as
findings, not guesses, and verify before adding more.

Full details in [`schema.md`](schema.md).

## Configuration

Three files, split by whether they're personal or reference data:

| File | Committed? | Why |
|---|---|---|
| `config/etfs.yaml` | no — gitignored | Someone's actual holdings |
| `config/tracked_tickers.yaml` | no — gitignored | Same, and optional |
| `config/yfinance_exchange_suffixes.yaml` | **yes** | Generic reference data, useful to everyone |

`.example` templates are committed for the first two. Paths are environment-overridable via
`settings.py`, which is also where `DATABASE_URL` comes from — no credential is ever read from a
config file or hardcoded.

`config_loader.py` validates on load and raises `ConfigError` with the specific problem. Two
loaders read the same file: `load_exchange_suffixes` and `load_ticker_overrides` take different
top-level keys from `yfinance_exchange_suffixes.yaml`, since a one-off ticker rewrite and an
exchange mapping are the same kind of reference data maintained together.

## Deployment

```mermaid
flowchart TB
    subgraph host["docker compose"]
        subgraph af["Airflow (custom image)"]
            API["apiserver"]
            SCHED["scheduler"]
            DAGP["dag-processor"]
            TRIG["triggerer"]
        end
        AFDB[("airflow-postgres<br/><i>Airflow's own metadata</i>")]
        APPDB[("postgres<br/><i>the data</i>")]
        PGA["pgadmin"]
    end

    API & SCHED & DAGP & TRIG --> AFDB
    SCHED -->|"BashOperator:<br/>uv run data-store ..."| APPDB
    PGA --> APPDB
```

**Two separate Postgres instances.** Airflow's metadata database and the data warehouse share
nothing. Airflow's is disposable; the data one is not.

**Airflow runs a custom image** (`Dockerfile.airflow`) that layers `uv` and this workspace onto the
official Airflow image. The DAGs shell out to the same `data-store` CLI you run locally, so what
executes in production is what you can reproduce by hand. The alternative — importing `data_store`
into Airflow's own interpreter — isn't currently possible anyway: the image ships Python 3.13 and
this workspace requires 3.14.

The cost of that choice is that **Airflow only learns whether the shell exited zero.** Row counts,
skips and failures exist only as log text, so you can't alert on "the daily run saved zero rows".
That's a known limitation with a planned fix (moving to `@task.external_python`, which returns a
value through XCom while keeping the interpreter isolation).

### Build-time vs. runtime, a real gotcha

`dags/` is bind-mounted, so DAG edits are picked up live. **Everything else is baked into the image
at build time** — including `data_store/` *and* `config/`. So:

```bash
docker compose build   # required after changing application code OR config/*.yaml
```

Editing `etfs.yaml` and triggering a DAG without rebuilding runs the *old* config. This is worth
fixing by bind-mounting `config/` the way `dags/` already is.

## Cross-cutting decisions

**Output is `print(..., flush=True)`, not `logger.info`.** There's no `logging.basicConfig`
anywhere, so `logger.info` is dropped by Python's default configuration — confirmed by a live
Airflow task producing no output. Only stdout survives the subprocess boundary. `logger.warning`
and above remain correct for genuine anomalies, since those *are* visible by default. See
[`../.claude/rules/logging.md`](../.claude/rules/logging.md); this is the constraint the planned
`@task.external_python` migration removes.

**Every flow reports a completion summary, including when it did nothing.** A run that legitimately
had nothing to do and a run that failed silently must never look the same. This is why an empty
resolved ticker set prints a line rather than returning quietly, and why a `--ticker` value matching
nothing is warned about by name.

**Failures are isolated at the natural unit of work** — per fund when scraping, per source-and-type
group when ingesting, per ticker when backfilling. One failure never costs the work queued behind
it, and the summary reports the failure count. Configuration errors are the exception: an unknown
provider is fatal, because it will fail identically on every retry.

**Transactions are per flow-step.** Each `save_*` commits, so an interrupted run leaves completed
work durable. Combined with the loading strategies in [`schema.md`](schema.md), every flow is
re-runnable without cleanup.

## Testing

Split by what they need, and the split is enforced by a marker:

| | Needs | Covers |
|---|---|---|
| `tests/unit/` | nothing | Logic, resolution rules, output, error handling — with fake connections and stubbed sources |
| `tests/integration/` | live Postgres | What a fake can't: `COPY` upserts, `ON CONFLICT` semantics, migration idempotency |

`pytest` deselects `integration` by default, so a plain run is *not* the whole suite. CI runs both,
the second against a service container. Details in [`../README.md`](../README.md).

The unit tests stub at the layer boundaries above, which is the practical payoff of having them:

- **Repository tests** fake the connection and cursor, so the SQL that gets built and the rows
  handed to `COPY` stay observable — the assertions are about what was sent to the database, not
  about a mock having been called.
- **Pipeline tests** stub the `save_*` functions and swap the source registries for fakes. That
  leaves orchestration decisions — which tickers resolve, how they group, what gets skipped, what
  gets printed — testable with no database and no network.

Progress output is treated as behaviour and asserted with `capsys`/`caplog`, on the grounds that a
run's summary is as much a part of its contract as its return value.

## Where to start reading

1. `pipeline.py` — the five flows; everything else is in service of these.
2. `cli.py` — how a command becomes a flow, and where configuration is assembled.
3. One domain module end to end (`prices/` is the most representative).
4. [`schema.md`](schema.md) — why the tables load the way they do.
