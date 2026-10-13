# data-store architecture

How this service is put together and why. For *what the tables look like* see
[`schema.md`](schema.md); for *how to run it* see [`../README.md`](../README.md). This document
covers the parts neither of those does: the layering, the boundaries between modules, and the
decisions that would otherwise look arbitrary.

## What the service is responsible for

Getting external financial data into Postgres, correctly and repeatably. Three things go in:

- **ETF holdings** — scraped from a fund provider's website (iShares today).
- **Raw daily market data** — OHLCV prices, dividends and stock splits, for every ticker those
  holdings contain plus directly tracked ones. One fetch, one table (`raw_market_data`) —
  unprocessed, matching the source's own grain, not decomposed into a return calculation there.
- **Daily FX rates** — currency-conversion rates for every currency in `config/currencies.yaml`,
  plus every enabled tracked ticker's currency, against its `base_currency`, landed in `fx_rates`. Kept separate from `raw_market_data`
  deliberately — an FX rate isn't a security's price and has no OHLC, dividends, or splits to
  carry.
- **Macro indicators** — monthly/quarterly US inflation and GDP (FRED) and EU/euro-area
  inflation and GDP growth (Eurostat), landed verbatim in `raw_macro_indicators`.

Two things are derived, not fetched: **`market_returns`** — split-adjusted price and
price/dividend/simple/log returns, rebuilt from `raw_market_data` by `calculate-returns` — and
**`macro_indicator_rates`** — comparable year-over-year inflation/growth percentages, rebuilt from
`raw_macro_indicators` by `calculate-macro-indicator-rates` (FRED's raw levels need the YoY
computed; Eurostat's chosen dimension slices already are one, and pass through unchanged). Both
still fit the service's remit because they involve no judgment about portfolios or holdings — it's
arithmetic on data already landed here, kept in this service so each raw table's sharp edges (the
split-restatement behaviour below for prices; the level-vs-rate distinction for macro data) are
handled once, next to the table that has them, rather than relearned by every consumer.

It does *not* value portfolios or decide what's worth owning — that belongs to `portfolio`, which
reads `market_returns`. The dividing line is deliberate: this service's job is that the data in
Postgres is a faithful, complete record of what the sources said, plus the one derived table close
enough to that record to need no portfolio-level context.

## Layering

Dependencies point one way, downward. Nothing in a lower layer imports from a higher one.

```mermaid
flowchart TD
    subgraph entry["Entry points"]
        CLI["cli.py<br/><i>argparse, one handler per command</i>"]
        DAGS["dags/*.py<br/><i>Airflow, shells out to the CLI</i>"]
        API["api/<br/><i>FastAPI, argument-parsing-plus-a-call, same as cli.py</i>"]
    end

    subgraph orch["Orchestration"]
        JOBS["jobs.py<br/><i>runnable units: config + connection + one flow</i>"]
        PIPE["pipeline.py<br/><i>the run-level write flows; currency resolution</i>"]
        READ["read_flows.py<br/><i>read-side composition: FX derivation, ticker info</i>"]
        ONB["ticker_onboarding.py<br/><i>tracked_tickers: YAML sync, onboarding</i>"]
    end

    subgraph domain["Domain modules"]
        HOLD["holdings/<br/>scraper · parser · repository"]
        MKT["market_data/<br/>yfinance_source · repository"]
        FX["fx/<br/>yfinance_source · repository"]
        RET["returns/<br/>calculator · repository"]
        REG["regimes/<br/>loader · repository"]
        MACRO["macro/<br/>fred_source · eurostat_source · repository · calculator"]
        TRK["tracked_tickers/<br/>yfinance_source (lookup) · repository"]
    end

    subgraph found["Foundation"]
        CONF["config_loader.py"]
        SET["settings.py"]
        SCHEMA["db/schema.sql"]
        RESULT["run_result.py"]
        RETRY["yfinance_retry.py<br/><i>shared transient-error classifier</i>"]
        RESOLVE["ticker_resolution.py<br/><i>holding → provider ticker</i>"]
    end

    subgraph ext_pkg["services/shared (workspace package)"]
        DB["db/connection.py · logging_config.py"]
    end

    PG[("Postgres")]
    EXT(["External: iShares, yfinance"])
    EXT2(["External: FRED, Eurostat"])

    DAGS -->|subprocess| CLI
    CLI --> JOBS
    API --> JOBS
    JOBS --> PIPE
    JOBS --> READ
    JOBS --> ONB
    JOBS --> CONF
    JOBS --> SET
    JOBS --> DB
    JOBS --> SCHEMA
    PIPE --> HOLD
    PIPE --> MKT
    PIPE --> FX
    PIPE --> RET
    PIPE --> REG
    PIPE --> MACRO
    PIPE --> RESOLVE
    READ --> FX
    READ --> RET
    READ --> HOLD
    READ --> TRK
    READ --> RESOLVE
    READ --> ONB
    ONB --> TRK
    HOLD --> EXT
    MKT --> EXT
    FX --> EXT
    MACRO --> EXT2
    TRK --> EXT
    TRK --> RETRY
    MKT --> RETRY
    FX --> RETRY
    HOLD --> PG
    MKT --> PG
    FX --> PG
    RET --> PG
    REG --> PG
    MACRO --> PG
    TRK --> PG
    DB --> PG
```

`db/connection.py` and `logging_config.py` are not this service's own code — they live in
[`services/shared`](../../shared/docs/architecture.md), a workspace package this service depends
on rather than duplicates. `auth` depends on the same package for the same reason. Everything else
in the diagram above is `data-store`-specific.

**`jobs.py` is the seam that matters.** Each `run_*` there is one complete operation: load
settings and configuration, open a connection, run the flow, close the connection, return its
result. `cli.py` is argument parsing plus a call into it, and nothing else. An Airflow task will
be a call into it and nothing else. Anything that lived in only one of those two would be, by
construction, untested by the other — which is exactly how a CLI and a scheduled job drift apart.

**The read API's jobs deviate from that contract on purpose.** `get_price_series`,
`get_fx_series` and `get_ticker_info` are named `get_*` rather than `run_*`, and return a data
payload rather than `run_result.py`'s `{"status", "rows_saved"}` — there is nothing to save and
no run to summarize, only a query to answer. `read_flows.py` is a sibling of `pipeline.py`, not
an addition to it: `pipeline.py` is already close to the file-size guidance in
[`context-management.md`](../../../.claude/rules/context-management.md), and FX cross-rate
derivation is a read-side decision with no write-flow analogue.

**`ticker_onboarding.py` is a second sibling, for the same file-size reason.** It holds the flows
that bring a ticker into the `tracked_tickers` table and through onboarding (`tasks.md`, the
ticker-onboarding chunk): `sync_config_tickers` (the YAML-to-table sync), the symbol lookup a
user's request goes through, and the onboarding run (TK-5).

`tracked_tickers/` follows the standard three-file shape, with one twist: its `*_source.py` doesn't
fetch data to store, it *classifies* a symbol. `base.py`'s `TickerLookupSource.lookup()` returns
one of four outcomes:

- `found`: has prices, and can be held.
- `unsupported`: has prices, but is an index or a currency pair.
- `not_found`.
- `unavailable`: the provider didn't answer.

A provider's answer is never an exception, so "Yahoo didn't answer" can't be mistaken for "no
such symbol". The orchestration around it lives in `ticker_onboarding.py`:

- **Normalisation:** `normalize_symbol` whitelists and uppercases a symbol before it reaches the
  provider.
- **Caching:** `lookup_symbol` keeps definite answers in an in-process `LookupCache` for 15
  minutes. It never caches `unavailable`, which says nothing about the symbol.

**Known limitation:** the cache is per process. The read API runs one uvicorn worker today, so
that's one cache; a second worker would cost at most one extra provider call per symbol.

Its arguments are plain builtins, including `earliest_date` as an ISO string rather than a `date`.
That looks like a weaker type until you notice both callers hand it over as text — argparse from
the command line, Airflow from a DAG parameter — so parsing it inside the job keeps that
conversion in one place instead of two.

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

`returns/` deviates the other way: just `calculator.py` (pure arithmetic, no I/O) and
`repository.py`. There is no `base.py` because there's no provider Protocol to satisfy, and no
`*_source.py` because there's no external call — the input is `raw_market_data`, this service's
own table. Disclosed rather than silently different: a domain reading another domain's table
instead of an external source is a shape the original three-file split didn't anticipate.

`regimes/` has a similar two-file shape: `loader.py` (reads and validates a local CSV into
`RegimeConfig` dataclasses, following the `config_loader.py` vocabulary for errors and validation)
and `repository.py` (writes to the `regimes` table). There is no external provider — the only
source is a hand-curated CSV in `config/`, updated manually — and no base Protocol.

`macro/` combines both patterns seen so far, because it has both a provider-backed raw table and
a derived one: `base.py` (the `MacroIndicatorSource` Protocol, shared by both providers),
`fred_source.py` and `eurostat_source.py` (one `*_source.py` per provider, not one — the two
talk to genuinely different APIs, a fixed-series pull vs. a dimension-filtered dataset query —
and `repository.py` (raw upsert plus the read-back `get_raw_indicators` the calculator needs),
plus `calculator.py` (pure YoY-normalization arithmetic, no I/O, mirroring `returns/calculator.py`
exactly). `retry.py` holds the transient-error classifier both `*_source.py` files share — scoped
to this domain rather than promoted to a foundation module like `yfinance_retry.py`, since both
callers live inside `macro/` itself rather than spanning two domains the way `market_data/` and
`fx/` share `yfinance_retry.py`.

`fx/` follows the standard three-file shape exactly, and is the second implementation of it (after
`market_data/`) rather than the first — deliberately kept as a *separate* domain rather than folded
into `market_data/`: an FX rate has no OHLC, dividends, or splits, so reusing `raw_market_data`'s
table and Protocol would mean either forcing zero-filled columns onto data that doesn't have that
shape, or narrowing the `MarketDataSource` Protocol in a way the trade-off below already explains
isn't free. `fx/yfinance_source.py` and `market_data/yfinance_source.py` do share one thing:
`yfinance_retry.py`'s `_is_transient` classifier, promoted out of `market_data/_yfinance_retry.py`
to a foundation module (not under either domain package) when `fx/` needed it too — sibling domain
modules reaching into each other's private helpers wasn't a foundation worth building on.

## Extension points

New providers are registry entries, not new branches. All three registries live at the top of
`pipeline.py`:

```python
SCRAPERS              = {"ishares": get_holdings_csv_url}
MARKET_DATA_SOURCES   = {"yfinance": YFinanceMarketDataSource}
FX_RATE_SOURCES       = {"yfinance": YFinanceFxRateSource}
MACRO_INDICATOR_SOURCES = {"fred": FredMacroIndicatorSource, "eurostat": EurostatMacroIndicatorSource}
```

Adding a second market-data (or FX-rate) provider means writing a class satisfying
`market_data/base.py`'s `MarketDataSource` (or `fx/base.py`'s `FxRateSource`) Protocol and adding
one line. No flow function changes. `FX_RATE_SOURCES` has one entry today and, unlike
`MARKET_DATA_SOURCES`, its key isn't selected from user config (`config/currencies.yaml` has no
per-currency `price_source` field — there was nothing to choose between yet) — `ingest_fx_rates`
and `backfill_fx_rates` both look it up under the literal `"yfinance"` key. Revisit that the day a
second FX source actually exists.

`MACRO_INDICATOR_SOURCES` is shaped differently from the other three registries: it isn't a
*choice* between interchangeable providers for the same data — `fred` and `eurostat` cover
disjoint regions (US vs. EU), so both are constructed unconditionally rather than one being
looked up by a config-selected key. Whether each is actually *called*, though, depends on
`eu_regions`: `ingest_macro_indicators` skips Eurostat's `fetch()` (and says so in its log) when
`config/macro_regions.yaml` is absent or empty — see the `macro_regions.yaml` note below.

**A disclosed trade-off of one combined registry**: before the OHLCV/dividend fetch merge, prices
and dividends had separate registries and Protocols, because they were genuinely separate
fetches with different `fetch()` shapes. Now that one fetch returns both, a future
price-only or dividend-only-shaped provider (e.g. a source with prices but no dividend concept)
has to implement the full `MarketDataSource` Protocol and return an empty/zero-filled frame for
whichever columns it doesn't have, rather than being a narrower Protocol of its own. Accepted
because it matches the actual shape of the one HTTP call each provider makes, and there's
currently exactly one provider (yfinance) to generalize from.

**One sharp edge if you add a source:** the registry *key* and the class's `.name` attribute are
two different strings. `.name` is what gets stamped into the `source` column and what backfill
floors are read under; the key is what config files reference. Keep them equal unless you have a
reason not to.

## The flows

Nine live in `pipeline.py`. Two, the tracked-ticker sync and ticker onboarding, live in
`ticker_onboarding.py`. All are exposed as CLI subcommands.

| Flow | Command | Cadence | Reads | Writes |
|---|---|---|---|---|
| Holdings ingest | `ingest-holdings` | weekly | provider site | `etf_holdings` |
| Market data ingest | `ingest-market-data` | daily, in parallel with regimes and FX ingest | `etf_holdings` + `tracked_tickers` (`ready` rows only) | `raw_market_data` |
| Market data backfill | `backfill-market-data` | manual | `raw_market_data` + `tracked_tickers` (every enabled row) | `raw_market_data` |
| Returns calculation | `calculate-returns` | daily, immediately after market data ingest succeeds (same run) | `raw_market_data` | `market_returns` |
| Regimes ingest | `ingest-regimes` | daily, in parallel with market data and FX ingest | `config/regimes.csv` | `regimes` |
| FX rates ingest | `ingest-fx-rates` | daily, in parallel with market data and regimes ingest | `config/currencies.yaml` + `tracked_tickers` currencies | `fx_rates` |
| FX rates backfill | `backfill-fx-rates` | manual | `fx_rates` | `fx_rates` |
| Macro indicators ingest | `ingest-macro-indicators` | monthly, own DAG | FRED + Eurostat + `config/macro_regions.yaml` | `raw_macro_indicators` |
| Macro indicator rates | `calculate-macro-indicator-rates` | monthly, immediately after macro ingest succeeds (same run) | `raw_macro_indicators` | `macro_indicator_rates` |
| Tracked-ticker sync | `sync-tracked-tickers` | at the start of every market data ingest, backfill and onboarding run; also runnable on its own | `config/tracked_tickers.yaml` | `tracked_tickers` |
| Ticker onboarding | `process-ticker-requests` | every 5 minutes, own DAG (`ticker_onboarding`) | `tracked_tickers` (due `pending` rows) | `raw_market_data`, `fx_rates`, `market_returns`, `tracked_tickers` |

**Returns calculation is the odd one out**: it reads no config and does no ticker resolution — it
discovers both sources and tickers directly from `raw_market_data` (`returns/repository.py`'s
`get_sources`/`get_tickers`), and is always a **full rebuild** of whatever tickers it's asked
about, never an incremental append. A split retroactively changes the correct value of every
earlier row for that ticker (see `docs/schema.md`, `market_returns`), so an incremental design
would serve stale values after a split rather than merely being slower to catch up. It's batched
by ticker (`RETURNS_BATCH_SIZE`, a few hundred at a time) rather than loaded as one DataFrame,
since `raw_market_data` is 8.95M rows — and a batch's save failure is isolated from the batches
around it the same way a ticker's failure is isolated in backfill.

Before the market-data merge shipped, price and dividend ingest/backfill were four separate flows,
each resolving the same ticker universe and hitting yfinance separately. They merged into one
because a single `Ticker.history()` call already returns OHLCV, dividends and stock splits together
— fetching them through two flows was paying for two HTTP round trips per ticker to get data that
came back in one.

**Market data ingest has a second, conditional fetch.** Normally it is one fetch per
(source, asset_type) group. But the provider retroactively rescales a ticker's *entire* price and
dividend history when that ticker splits (see [`schema.md`](schema.md), "What `close` actually
contains"), while this flow only re-fetches a rolling window — so a split would leave one ticker's
stored rows on two different scales, a step change indistinguishable from a real
several-hundred-percent move. When an ingested batch contains a non-zero `stock_splits`, the flow
therefore re-fetches that ticker's full stored range and rewrites it
(`pipeline._refresh_split_histories`). This is the one place the daily flow issues an unbounded
historical request rather than a windowed one; splits are rare enough that the cost is negligible.

**Market data ingest, regimes ingest, FX rates ingest and returns calculation share one DAG,
`daily_pipeline`.** `ingest-market-data`, `ingest-regimes` and `ingest-fx-rates` start together
with no dependency between any pair of them — a hand-curated CSV of market regimes, a currency
conversion rate, and a security's price have nothing to do with each other, so there is no reason
for one to wait on another, and running them in parallel gets all three done faster.
`calculate-returns` runs afterward, but is gated specifically on `ingest-market-data`'s result
check succeeding, not merely on the task finishing: it rebuilds `market_returns` from whatever
`ingest-market-data` just landed in `raw_market_data`, so an "ok" ingest that silently saved zero
rows must not unlock a returns rebuild any more than a raised exception would. It has no
dependency on the regimes or FX branches, which touch different, unrelated tables. Each
`ingest-regimes` run still replaces the entire `regimes` table from the current CSV file — the
source is still a hand-curated CSV, edited manually when new market events occur or existing
regime definitions change, but the load into the database is now scheduled daily like the other
data-source flows rather than triggered by hand after every edit. `ingest-fx-rates` has its own
manual-trigger counterpart, `backfill-fx-rates` (DAG `backfill_fx_rates`), mirroring
`backfill-market-data`'s shape — one currency pair at a time, rate-limited, using `fx_rates` itself
as the checkpoint via `get_earliest_fx_dates` — but without market data's split-restatement
concern, since an FX rate has no split to restate.

**Macro indicators get their own DAG (`macro_indicators`), scheduled `@monthly` rather than
joining `daily_pipeline`.** CPI/HICP publish monthly and GDP quarterly — nothing here changes
day to day, so a daily run would mostly re-save identical figures. `ingest-macro-indicators`
lands `raw_macro_indicators`; `calculate-macro-indicator-rates` runs after, gated on the ingest's
check succeeding, the same pattern as `calculate-returns` after `ingest-market-data`. No backfill
counterpart exists: FRED and Eurostat both return a series' full available history in one call
(unlike yfinance, there's no per-ticker rate-limit concern driving a slow, one-at-a-time backfill
mode), so `ingest-macro-indicators` already fetches everything available each run.

**Ticker onboarding gets its own DAG (`ticker_onboarding`), every 5 minutes, one run at a
time.** A user who adds a ticker should wait minutes, not until tomorrow's `daily_pipeline`.
Each run:

1. syncs the YAML;
2. returns any row left `backfilling` for over an hour (a run that died) to `pending`;
3. takes up to 50 due `pending` rows, oldest request first. For each it:
   - claims the row (`pending` → `backfilling`, a conditional update, so two runs can never both
     work on one ticker);
   - backfills the ticker's full history by reusing `backfill_market_data` with
     `only_tickers` and no funds;
   - backfills its currency's FX rates, if it isn't the base currency;
   - calculates its returns;
   - marks it `ready`.

Why it's shaped this way:

- **Success is judged by what landed, not by the flows' statuses.** The row only becomes `ready`
  once `raw_market_data` holds prices for it and `fx_rates` holds the pair. Only then can a
  portfolio value it.
- **The recorded reason says whether Yahoo answered.** A backfill that couldn't reach the provider
  (`tickers_unreachable`) is saved as "the provider couldn't be reached"; one that reached it and
  got nothing is "the provider returned no price history". They were once the same message, so a
  DNS failure right after the host woke from sleep read to an admin as a bad symbol.
- **A failed attempt is retried after 10, then 20 minutes, and given up (`failed`) on the 3rd.**
  Only giving up makes the run `partial` and the DAG red. A retry still to come is a warning, so
  a passing Yahoo blip doesn't page anyone, while a ticker that will never load does.
- **A config row arrives with no currency**, since the YAML doesn't carry one. Onboarding looks
  it up first (the same lookup a user's request uses) and stores it with the exchange. Failing to
  is a warning, not a failed attempt, because the prices can still load.
- **Why not trigger a backfill from the API directly?** A queue table read by a frequent DAG
  survives restarts, keeps retries and logs in Airflow with the rest of the pipeline, needs no
  Airflow credentials in the API container, and never waits behind a multi-hour full backfill.
  The cost is up to five minutes' delay, which the user sees as "loading".

### What a flow returns

The four run-level flows return a structured result, not a row count:

```python
{"status": "ok" | "partial" | "nothing_to_do",
 "tickers_resolved": int, "rows_saved": int, ...}   # plus per-flow counts
```

`status` carries the judgement so callers don't re-derive it from counts. Zero rows saved is
ambiguous on its own — it is the correct outcome for a run with nothing to do, and an alarm for a
run whose every group failed. Separating them is what lets an automated check react to the second
without crying wolf about the first, and it's the same distinction the logging rules require of the
human-readable output.

A request that matches nothing is a **failure**, not an empty success: asking for
`--ticker NOPE` and getting nothing means the run didn't do what it was asked, so it reports
`partial` rather than `nothing_to_do`. More generally a failure outranks emptiness — a run that
ended up with nothing to work on *because* something broke must not show green.

`run_result.py` owns this contract — `RUN_RESULT_KEYS` names the two keys *every* run carries, so
a caller can react to any of them without knowing which ran. Only two, deliberately: a pricing run
counts tickers and a holdings run counts funds, so a shared "units" key would mean something
different in each and be a trap rather than a contract. Each run adds its own named counts on top
(`tickers_resolved`, `groups_failed`, `funds_failed`, and so on).

It lives in its own module rather than in `pipeline.py` because four layers depend on it —
`pipeline` builds these, `jobs` builds and forwards them, the CLI turns them into exit codes, and
Airflow reads them back as XCom values. One definition is what stops those drifting.

**The value is plain builtins on purpose.** It is destined to cross a process boundary as an
Airflow XCom — pickled in this service's interpreter and unpickled in Airflow's, where
`data_store` is not importable. A dataclass, enum, `date` or numpy integer would fail there with
`ModuleNotFoundError`. A test asserts the contract rather than trusting it.

`ingest_holdings` is the exception: it stays a bare row count because it is a per-fund primitive,
not a run. Run-level aggregation across funds belongs to its caller.

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
    Y["tracked_tickers.yaml"] -->|"sync_config_tickers"| G
    G["tracked_tickers table<br/><i>already provider-ready</i>"] --> Z
```

Three things about this are intentional:

1. **An unmapped exchange is skipped with a warning, never guessed.** The provider returns *no
   rows* rather than an error for a bad symbol, so a guessed ticker would look like a ticker that
   simply had no trading activity. Refusing to guess is what keeps "no data" meaningful.
2. **Resolution happens at fetch time, not at scrape time.** `etf_holdings.holding_ticker` stays
   exactly as scraped, so fixing a mapping takes effect on the next pricing run with no re-scrape.
3. **Tracked tickers bypass it entirely.** They're configured in provider-ready form already, so
   putting them through exchange resolution would corrupt them.

**Tracked tickers are read from the `tracked_tickers` table, not the YAML.** Each market data
ingest and backfill first syncs `tracked_tickers.yaml` into the table, then reads it back, so the
YAML keeps working as the default list while users and admins can add rows of their own. The two
flows read it differently, on purpose:

- **The daily ingest takes only `enabled` rows whose `status` is `ready`.** Backfill's resume rule
  skips any ticker that already has data. If the ingest landed a 7-day window for a ticker that
  hadn't been backfilled yet, the backfill would then see data, skip it, and its history would
  never be fetched. Keeping not-yet-ready tickers out of the ingest is what makes "backfill
  first" hold.
- **Backfill takes every `enabled` row**, ready or not, since filling the history is what makes a
  ticker ready.

A consequence: a ticker added to the YAML is not priced daily until onboarding (TK-5) has
backfilled it and marked it `ready`.

The rules live in `ticker_resolution.py`, not `pipeline.py`, because the read API needs the
same answer (which holding a priced ticker came from, for its currency). They return `None` for
an unmapped exchange without logging; ingest warns at its own call site. The read API resolves
every holding on each call, so a warning there would only be noise.

The per-exchange normalizers (`TICKER_NORMALIZERS`) encode verified quirks — Hong Kong codes need
zero-padding, London tickers ending in `.` must not double it, Nordic and Toronto share classes use
a hyphen. These were confirmed against real provider responses, not inferred. Treat them as
findings, not guesses, and verify before adding more.

Full details in [`schema.md`](schema.md).

### How yfinance answers a symbol lookup (verified)

Ticker onboarding (`tasks.md`, TK-3) has to tell "Yahoo doesn't know this symbol" apart from
"Yahoo didn't answer". These are observed responses (yfinance 1.7.0, 2026-10-10, with
`hide_exceptions = False` as `market_data/yfinance_source.py` sets it), not inferences:

| Symbol | `Ticker(s).history(...)` |
|---|---|
| Unknown (`NOTAREALTICKER123`) or long-delisted (`TWTR`) | Raises `curl_cffi` `HTTPError`, `response.status_code == 404` |
| Recently delisted (`SIVB`) | Raises `YFPricesMissingError` (`period="5d"`) or `YFTzMissingError` (`period="max"`), both subclasses of `YFTickerMissingError` |
| Throttled | `YFRateLimitError("Too Many Requests. Rate limited. ...")`, read from the installed source; not provoked live |
| Valid | Rows, plus `history_metadata` with `symbol`, `currency`, `exchangeName`, `fullExchangeName`, `instrumentType`, `longName`, `shortName` |

What follows from it:

- **A 404 is an `OSError`.** `curl_cffi`'s `HTTPError` subclasses `OSError`, so
  `yfinance_retry._is_transient` classifies an unknown symbol as *transient*. A lookup must check
  for a 404 (and for `YFTickerMissingError`) **before** asking `_is_transient`, or every typo would
  read as "Yahoo is down, try again". That check is `yfinance_retry._is_missing_symbol`, shared by
  the lookup and the market-data source; `_is_unreachable` is "transient and not missing". The
  market-data source still retries a 404 like a network blip (wasted calls, nothing worse), but
  doesn't report it as unreachable, so a delisted holding stays "no data" in a backfill.
- **`instrumentType` values seen**: `EQUITY`, `ETF`, `CRYPTOCURRENCY`, `MUTUALFUND`, `FUTURE`,
  `INDEX`, `CURRENCY`. The first five can be held; an index or a currency pair can't.
- **`currency` is per listing, and can be a minor unit.** `VOD.L` is `GBp` (pence), `VUSA.L` is
  `GBP` and `CSPX.L` is `USD`, all on the LSE. So a lookup has to show the currency for the user to
  confirm. Nothing in the system rescales a minor-unit price yet; see `tasks.md`'s known
  limitations.
- **Symbols are case-insensitive** (`asml.as` works), and `history_metadata["symbol"]` returns the
  canonical spelling (`ASML.AS`), which is what should be stored.
- `longName` can be `null` (`GC=F`), so a display name falls back to `shortName`.
- A valid lookup took 0.1–0.6 s and a 404 about 0.9 s, which is fast enough for a request path.

## Read API

A fourth entry point, alongside `cli.py` and `dags/*.py`: a FastAPI app (`api/`) over tables this
service already owns, for `portfolio` and `web` to consume over HTTP rather than opening a
database connection of their own — the cross-service boundary `portfolio/docs/architecture.md`
insists is always an API, never a shared table.

It is mostly read-only. The exception is the tracked-ticker routes (below), which add and change
`tracked_tickers` rows. They're still not a backfill trigger: a request only queues a ticker, and
the `ticker_onboarding` DAG does the fetching. So the API process never makes a long provider
call beyond one quick lookup.

| Endpoint | Backed by | Notes |
|---|---|---|
| `GET /prices/{ticker}` | `market_returns` | Not `raw_market_data` — schema.md calls `market_returns` "the table a portfolio or chart should read"; serving raw OHLCV would push return computation onto every consumer. |
| `GET /fx/{base}/{quote}` | `fx_rates` | See below — the endpoint derives pairs `fx_rates` never stores directly. |
| `GET /tickers` | `raw_market_data` | Every ingested ticker for `source`, sorted and unpaginated (~1.3k tickers, ~10 KB). Built for `portfolio`'s manual-entry picker, which needs the whole set at once. Uses the same "ingested universe" definition as `GET /tickers/{ticker}`'s `exists` (`returns/repository.py`'s `list_tickers` sits next to `ticker_exists`), so a ticker in the list always reports `exists: true`. The query is a recursive-CTE loose index scan over the primary key: Postgres 17 has no skip scan, and a plain `SELECT DISTINCT` would read all ~9M rows to find the ~1.3k tickers. Measured at ~25 ms on the dev database. |
| `GET /tickers/{ticker}` | `raw_market_data` + `market_returns` + `tracked_tickers` + `etf_holdings` | Existence and whether returns have been computed are reported separately — a ticker can be ingested before `calculate-returns` has run for it. `status` is the ticker's onboarding status when it's tracked, else `null`. `currency` and `fx_currency`: see below. |
| `GET /tickers/{ticker}/lookup` | `tracked_tickers`, then the provider | What adding a ticker would do. `outcome` is one of: `tracked` (with its `status`), `available`, `unsupported` (an index or a currency pair), `not_found`, or `unavailable` (the provider didn't answer). Always 200, except 422 for a symbol that can't be one. |
| `POST /tracked-tickers` | `tracked_tickers` | Adds a user's ticker as `pending`. 201 when added, 200 when already tracked (free, and not counted). Errors: 404 not on the provider, 422 invalid or can't be held, 429 over the requester's daily limit, 503 provider unavailable. |
| `GET /tracked-tickers` | `tracked_tickers` | Filterable by `requested_by`, repeated `status`, repeated `ticker`. |
| `PATCH /tracked-tickers/{ticker}` | `tracked_tickers` | Admin changes: `enabled`, or `retry` a `failed` ticker. 409 for enabling or disabling a config row (the next YAML sync would undo it) or retrying one that isn't `failed`. |

**A ticker's currency** comes from `tracked_tickers.currency` when that is set. Otherwise it comes
from the newest `etf_holdings` row that resolves to the ticker, else `null`. It is the
provider's spelling verbatim (`GBp` stays `GBp`). `fx_currency` is
`ticker_onboarding.major_currency(currency)`, the code `fx_rates` is keyed by. That function is
the one minor→major map; a consumer picks its FX pair from `fx_currency` instead of keeping a
copy (portfolio's Labs backtest does this).

- **Holdings are matched by resolving forward**, with ingest's own rules (`ticker_resolution.py`),
  not by stripping the suffix. iShares reuses a raw symbol across exchanges (`DG` is Vinci in
  Paris and Dollar General in New York), and overrides and normalizers can't be reversed. All
  ~1.3k listings are resolved on each call, which is cheap.
- **Every snapshot counts, not just the latest**, so a holding that left the index keeps a
  currency for its stored history.
- **iShares writes `GBP` where yfinance writes `GBp`** (`AZN`, `SHEL` on the LSE), so a
  holdings-only London ticker reports `GBP`. This does no harm: a percentage return doesn't
  depend on the unit, and `fx_currency` is `GBP` either way. A price in that currency is still
  in pence.

**`source` is an explicit, defaulted query parameter, not hardcoded.** `source` is part of the
primary key on all three tables — the schema's own hook for a second provider to coexist with
`yfinance` for the same ticker/pair/date. Hardcoding it would silently return duplicate dates the
day a second source exists.

**The FX endpoint owns cross-rate derivation**, rather than pushing schema.md's documented
division formula onto every caller. `fx_rates` only ever stores `(configured_base, X)` pairs
(see schema.md's "No cross-rates stored"), so a request for any other pair is satisfied one of
four ways:

```mermaid
flowchart LR
    Q{"base == quote?"} -->|yes| ID["identity: rate = 1"]
    Q -->|no| B{"base is the<br/>configured base?"}
    B -->|yes| DIR["direct: fx_rates row, verbatim"]
    B -->|no| C{"quote is the<br/>configured base?"}
    C -->|yes| INV["inverse: 1 / rate"]
    C -->|no| TRI["triangulated:<br/>rate(cfg_base, quote) / rate(cfg_base, base)"]
```

This is exactly the identity schema.md documents for a cross-rate (e.g. `rate(GBP, CHF) =
rate(EUR, CHF) / rate(EUR, GBP)` when `EUR` is the base) — the API just applies it once instead
of leaving every future caller to re-derive it. The response's `derivation` field names which
path produced the numbers, and `via` names the currency triangulated through when it applies, so
a caller can tell a real stored rate from a computed one without knowing the formula itself. An
identity request (`base == quote`) has no `fx_rates` row of its own to answer from — the trading
calendar it reports rate `1` for comes from `get_dates_with_rate`, every date the requested
currency appears on either side of a stored pair.

Two details fall out of deriving rather than reading:

- **A stored rate that isn't a usable quote (`<= 0`) drops its date from every derivation**, with
  a warning, rather than being served verbatim on the direct path and dividing by zero on the
  inverse and triangulated ones. `save_fx_rates` only rejects rates too *large* for the column,
  so a `0` can reach the table; serving it would just move the division by zero into the caller.
  This mirrors the write side's own convention — skip the row, warn, keep the rest of the range.
- **Derived rates are quantized to `fx_rates.rate`'s `NUMERIC(18,8)` scale.** A `Decimal`
  division carries the default context's 28 significant digits, which would have made a
  triangulated pair look far more precise than a stored one when both only ever had 8dp of truth
  behind them. Every response is on one scale as a result.

**Errors follow the service's existing "a match on nothing is a failure" stance**
(see "What a flow returns" above): a ticker with no ingested rows at all, or a currency outside
the FX universe (below), is a 404 — a typo must be loud. A valid ticker or pair
whose requested date range simply has no rows is a 200 with an empty `points` list — a quiet
range is not a broken one, the same distinction the write flows draw between "nothing to do" and
"failed".

**Security**: every route but `/health` requires `Authorization: Bearer <INTERNAL_API_SECRET>`,
compared with `secrets.compare_digest`. This is the same trust model `auth/docs/architecture.md`
describes for `auth`/`portfolio`: the secret proves "a legitimate internal caller", not
cryptographically that a specific caller made the request — an accepted gap, not an oversight,
revisited on the same trigger `auth`'s doc names (before this deployment is reachable beyond a
single trusted host, or before real user data is involved).

**`/docs`, `/redoc` and `/openapi.json` are switched off**, so that "every route but `/health`"
is literally true. FastAPI mounts them as plain Starlette routes, which neither the router's
dependency nor an app-level one covers — they served the full route and parameter surface to
anyone. Putting them behind the bearer isn't an option either: Swagger UI fetches the schema
from the browser with no way to attach the secret. For a service-to-service API whose contract
is already written down in README.md and this file, an interactive explorer is not worth an open
endpoint.

**Known limitations, stated plainly rather than oversold:**

- **One connection per request**, opened and closed the same way every `run_*` job does. Fine at
  today's load; `psycopg_pool` is the intended fix if request volume ever makes per-request
  connection setup a bottleneck.
- **`calculated_at` is not a usable freshness signal** for a price series. `market_returns`'s
  upsert never deletes (see "Cross-cutting decisions" below), and the column only advances on a
  row that was actually rewritten — a caller cannot tell "just computed" from "computed weeks ago
  and untouched since" by looking at it.
- **The shared secret is caller-agnostic.** Any holder of it can query any ticker or currency;
  there is no per-caller scoping, matching `auth`'s own accepted gap rather than inventing a
  stricter model data-store alone would need to maintain.

**Who may add a ticker, and how often, is the caller's word.**

- `POST /tracked-tickers` takes `requested_by` (an `auth` user id) and `enforce_limit`. `web` sets
  `enforce_limit` to false for admins, through `portfolio`.
- This service enforces the limit (`DATA_STORE_TICKER_REQUESTS_PER_DAY`, default 10 per rolling
  24 hours) but doesn't decide who is exempt, the same trust model the ticker-mapping routes use.
  It counts the rows a user created, so adding a ticker that is already tracked costs nothing.
- The limit is checked before the provider is asked, which spares the rate limit, and the
  provider is asked again on add rather than trusting an earlier lookup.
- Two simultaneous adds by one user can both pass the count. That's accepted: the limit exists to
  stop volume, not to be exact.

## Configuration

Four files, split by whether they're personal or reference data:

| File | Committed? | Why |
|---|---|---|
| `config/etfs.yaml` | no — gitignored | Someone's actual holdings |
| `config/tracked_tickers.yaml` | no — gitignored | Same, and optional. The platform's default tracked tickers, synced into the `tracked_tickers` table rather than read directly |
| `config/currencies.yaml` | no — gitignored | Someone's own base/tracked currencies |
| `config/yfinance_exchange_suffixes.yaml` | **yes** | Generic reference data, useful to everyone |
| `config/macro_regions.yaml` | no — gitignored | Someone's own choice of EU regions, and optional |

`.example` templates are committed for the first three. Paths are environment-overridable via
`settings.py`, which is also where `DATABASE_URL` comes from — no credential is ever read from a
config file or hardcoded.

**The FX universe is `currencies.yaml` widened by tracked tickers' currencies.** A ticker
nobody can convert into the base currency can't be valued, so `currencies_with_tracked_tickers`
(`ticker_onboarding.py`) adds the currency of every enabled `tracked_tickers` row before FX
ingest, FX backfill and `GET /fx` use the list. That way adding a Johannesburg stock doesn't also
need someone to edit a file on the server.

- Minor units map to their major currency first (`GBp`→`GBP`, `ZAc`→`ZAR`, `ILA`→`ILS`), since
  FX rates exist only for the major one. That picks the pair only: a pence-quoted price is still
  stored in pence, and nothing rescales it yet (a known limitation, in `tasks.md`).
- **A currency the YAML explicitly disables stays disabled**, with a warning naming the tickers
  it leaves unconvertible. `enabled: false` is an operator decision, and the ticker list
  shouldn't override it silently.
- The widening is done in the job layer, not `pipeline.py`. The FX flows still take a plain
  `CurrenciesConfig` and don't know where its entries came from.

`config/currencies.yaml` sits between the other two gitignored files in one way: it's required,
not optional (`load_currencies` raises `ConfigError` on a missing file, the same as `load_funds`)
— unlike `tracked_tickers.yaml`, there's no sensible "not configured yet" state for the currency
list an FX ingest needs to run at all. It names one `base_currency` and every other enabled
currency is tracked as a rate against it; see [`schema.md`](schema.md)'s `fx_rates` section for
the exact convention.

`config_loader.py` validates on load and raises `ConfigError` with the specific problem. Two
loaders read the same file: `load_exchange_suffixes` and `load_ticker_overrides` take different
top-level keys from `yfinance_exchange_suffixes.yaml`, since a one-off ticker rewrite and an
exchange mapping are the same kind of reference data maintained together.

**`INTERNAL_API_SECRET`** is the read API's shared secret, read via `settings.py` like every other
value here — never from a config file. It is `Settings.internal_api_secret: str | None`, optional
at the `Settings` level (every CLI/Airflow caller has no need of it, so making it required would
break them) but required by `api/app.py` at its own startup: the API fails loudly with
`SettingsError` if it's unset, rather than silently accepting every caller once the check would
otherwise run.

**`DATA_STORE_TICKER_REQUESTS_PER_DAY`** (default 10) is the per-user limit on new tickers
via `POST /tracked-tickers`. It's a behavioural setting rather than a credential, but lives in the
environment like the rest because it's per deployment. `load_settings` refuses a value that isn't
a non-negative integer, rather than letting the API fall back to some default.

**`FRED_API_KEY`** follows the identical pattern: `Settings.fred_api_key: str | None`, optional at
the `Settings` level (only `ingest-macro-indicators` needs it), but `run_ingest_macro_indicators`
raises `SettingsError` at its own point of use if it's unset — the same "fail loudly at the one
call site that needs it, not at every caller" shape as `INTERNAL_API_SECRET`.

**`config/macro_regions.yaml` is optional, unlike `config/currencies.yaml`.** `load_macro_regions`
itself returns `[]` for a missing or empty file, rather than splitting that check into `jobs.py`
the way `tracked_tickers.yaml` does — a deliberately simpler version of the same "not configured
yet" tolerance, since there was no second caller here that needed the file-existence check
separated out. FRED's US data ingests regardless; only the Eurostat half of a run is skipped
(and says so in its log) when this file is absent.

## Deployment

```mermaid
flowchart TB
    subgraph host["docker compose"]
        subgraph af["Airflow (custom image)"]
            AFAPI["apiserver"]
            SCHED["scheduler"]
            DAGP["dag-processor"]
            TRIG["triggerer"]
        end
        READAPI["api<br/><i>Dockerfile.api, uvicorn</i>"]
        AFDB[("airflow-postgres<br/><i>Airflow's own metadata</i>")]
        APPDB[("postgres<br/><i>the data</i>")]
        PGA["pgadmin"]
    end
    CALLER(["portfolio"])

    AFAPI & SCHED & DAGP & TRIG --> AFDB
    SCHED -->|"@task.external_python:<br/>jobs.run_* in /opt/stakehold/.venv"| APPDB
    PGA --> APPDB
    READAPI -->|"jobs.get_*"| APPDB
    CALLER -->|"HTTPS + bearer token"| READAPI
```

**Two separate Postgres instances.** Airflow's metadata database and the data warehouse share
nothing. Airflow's is disposable; the data one is not.

**`postgres` (the data warehouse, not `airflow-postgres`) runs with `log_statement=mod`**
(`docker-compose.yml`'s `command:`), logging every `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` —
including a direct `psql` connection bypassing `data_store`'s own code — to the container's own
log. See `auth/docs/architecture.md`'s "Deployment" section for the incident that prompted this
across all three app-data instances.

**Airflow runs a custom image** (`Dockerfile.airflow`) that layers `uv` and this workspace onto the
official Airflow image. The DAGs call the same `jobs.run_*` functions the CLI calls, through
`@task.external_python`.

**The read API is its own container** (`Dockerfile.api`), not folded into the Airflow image: it
serves live traffic on its own lifecycle, independent of whatever the scheduler is doing, and
needs none of Airflow's own image (no Playwright browser, no Airflow package at all — just this
workspace's dependencies via `uv sync` and `uvicorn`). Like the Airflow containers, it reaches
`postgres` by the compose network's service name, not `localhost:${POSTGRES_PORT}` — `DATABASE_URL`
is overridden in `docker-compose.yml` the same way `x-airflow-common`'s is.

**`api` also joins `stakehold-net`** (task 13's cross-service network, see `docs/smoke-test.md`),
opted into via `services/data-store/docker-compose.stakehold-net.yml` rather than baked into this
file directly — `portfolio` reaches it there as `http://data-store-api:8000` for `rebuild-returns`.
`web` never calls `data-store` directly (see `web/docs/architecture.md`'s "Talks to" section), so
only `portfolio`'s benefit justifies this. The Airflow containers and `pgadmin` stay off that
network — nothing cross-service talks to either.

**Two interpreters, on purpose.** Airflow's own is Python 3.13; this workspace requires 3.14 and
pulls in pandas, yfinance and Playwright. `@task.external_python` runs the task body in
`/opt/stakehold/.venv` while Airflow's environment stays clean — so a dependency change here can't
break the scheduler, and DAG parsing never imports pandas. `expect_airflow=False` is required
because that venv deliberately has no Airflow in it.

**And the task returns a value.** The operator pickles the function's return in the external
interpreter and unpickles it in Airflow's, where it becomes the task's XCom `return_value`. That
is why the result contract is plain builtins: `data_store` is not importable on the receiving
side, so a dataclass or `date` would fail to unpickle. Each DAG's final `check_run` task reads
that value and fails the run on a partial failure, or on an "ok" run that saved nothing — the
alert that was structurally impossible when a task could only report a shell's exit code.

A side effect worth noting: with no shell in the path, the `shlex.quote` escaping the old
`BashOperator` needed for `--ticker` values is gone entirely, along with the injection surface it
guarded.

### Build-time vs. runtime

`dags/` and `config/` are bind-mounted, so DAG edits and configuration changes take effect on the
next run. **Application code under `src/data_store/` is baked into the image**, along with every
other workspace member — `services/auth`, `services/portfolio`,
[`services/shared`](../../shared/docs/architecture.md), and `services/web` — even the ones this
service's code never imports at runtime. That's not caution, it's a hard requirement: the root
`pyproject.toml` depends on all of them (`package = false`), so `uv sync --frozen` inside the image
resolves the *whole* workspace lock and needs every member's source present to build it. One
missing from the `COPY` list isn't a smaller image, it's a build that fails at `uv sync` — the gap
this bit before `services/shared` existed, when `auth` and `web` were never copied in. So:

```bash
docker compose build   # required after changing src/data_store/ code
```

`config/` is mounted rather than baked because it changes far more often than code — adding a fund
or an exchange suffix is routine — and silently running yesterday's configuration is a
particularly unpleasant way to discover a build step you forgot.

**The `api` container follows the same rule.** `Dockerfile.api` `COPY`s the whole `services/`
tree at build time (every workspace member, not just `data-store` — the root `pyproject.toml`
lists all four as dependencies of the `package = false` root project, so `uv sync` needs each
one's source present even though the API only imports `data_store` itself), so it too needs
`docker compose build` after a code change; nothing under it is bind-mounted. Because that
`COPY` is service-agnostic, the root `.dockerignore` excludes `**/.env` rather than one named
service's file — any workspace member's secrets would otherwise be baked into the image.

## Cross-cutting decisions

**Logs go to stderr, results go to stdout.** Every entry point calls `configure_logging()`
(`shared.logging_config`), so `logger.info` is visible — in a plain CLI run and in Airflow's
captured task output alike. The one remaining `print` is a command's actual result (the row count
`cli.py` writes), which stays on stdout so the CLI is pipeable.

This replaced an earlier `print(..., flush=True)` convention that existed only because nothing
configured logging, which meant `logger.info` was silently dropped and `print` was the sole
mechanism that worked. Configuring it once removed the constraint. A side effect worth keeping:
progress and warnings now share one stream and are therefore ordered relative to each other —
previously a warning could surface in a captured log nowhere near the work it concerned. See
[`../.claude/rules/logging.md`](../.claude/rules/logging.md).

**Every flow reports a completion summary, including when it did nothing.** A run that legitimately
had nothing to do and a run that failed silently must never look the same. This is why an empty
resolved ticker set prints a line rather than returning quietly, and why a `--ticker` value matching
nothing is warned about by name.

**A partially failed run exits non-zero.** `nothing_to_do` is a success — a quiet day is not a
broken one — but `partial` is not, so whatever scheduled the command can tell the difference
without reading logs.

**Failures are isolated at the natural unit of work** — per fund when scraping, per source-and-type
group when ingesting, per ticker when backfilling, per ticker-batch when calculating returns, and
(since the market-data merge) per ticker within a group's *fetch* too: `YFinanceMarketDataSource`
retries and gives up on one ticker at a time, so a single permanently-failing ticker no longer
costs every other ticker fetched alongside it in the same group, the way one bad ticker used to
fail an entire batched `yf.download()` call. One failure never costs the work queued behind it, and
the summary reports the failure count.

A ticker the source gave up on comes back as an empty frame, exactly like one with no data, so
the source also lists the tickers it **couldn't reach the provider for** in `unreachable` (part of
the `MarketDataSource` Protocol, replaced on every `fetch()`). `backfill_market_data` counts those
as failed (`tickers_unreachable`, making the run `partial`), and still counts a ticker the
provider has nothing for, including an unknown or delisted symbol, as "no data". The split is
deliberate: over thousands of ETF holdings, some symbols are always missing from Yahoo, and
failing the run for them would make it red every day, while a network outage passing as "no
data" is exactly the silent success the run-result contract exists to prevent.
**Known gap:** `ingest_market_data` doesn't read `unreachable` yet, so a daily ingest during an
outage still reports those tickers as having no data. It's the next place to use it. Configuration errors are the exception: an unknown provider
is fatal, because it will fail identically on every retry.

**`market_returns`'s upsert never deletes.** A full rebuild self-heals any row whose inputs changed
or whose ticker is newly followed, but a row removed from `raw_market_data` leaves an orphaned row
behind in `market_returns` rather than being cleaned up. Not a normal occurrence today (rows aren't
currently deleted from `raw_market_data`), but worth stating plainly rather than overselling "full
rebuild" as a complete self-healing guarantee.

The post-split history refresh sits deliberately *outside* its group's `try`/`except`, as its own
isolation boundary. A repair fetch failing is not the group failing: the group's own rows are
already fetched, saved and correct, and attributing the failure to the group would misreport a
good day's ingest as a bad one. It is counted separately (`splits_refresh_failed`) and still makes
the run `partial`, because the ticker really is left in an inconsistent state. This is also the one
failure the flow cannot retry indefinitely — the trigger is the split row itself, which disappears
once it ages out of the rolling window — so the warning names the `--force-refresh` recovery path
rather than relying on a later run to notice. A durable pending-refresh table was considered and
rejected as disproportionate for an event this rare.

**Transactions are per flow-step.** Each `save_*` commits, so an interrupted run leaves completed
work durable. Combined with the loading strategies in [`schema.md`](schema.md), every flow is
re-runnable without cleanup. On failure, `save_raw_market_data` rolls back before re-raising —
Postgres leaves a transaction aborted after an error, so without an explicit rollback every later
statement on that connection would fail too, breaking the per-ticker isolation described above.
Callers that catch a save failure and keep going (backfill's per-ticker loop) depend on this to
leave the connection usable for the next ticker.

**Row-level validation guards COPY, not just fetch.** `save_raw_market_data` checks each row
against its columns' numeric ranges before handing it to `COPY`, dropping (and warning about) any
row that would overflow — e.g. a garbage `adjclose` Yahoo's API occasionally returns. `COPY` has no
partial-failure mode: one bad value aborts the whole staged batch, which would otherwise cost a
ticker's entire fetched history for the run over a single bad day.

## Testing

Split by what they need, and the split is enforced by a marker:

| | Needs | Covers |
|---|---|---|
| `tests/unit/` | nothing | Logic, resolution rules, output, error handling — with fake connections and stubbed sources |
| `tests/integration/` | live Postgres | What a fake can't: `COPY` upserts, `ON CONFLICT` semantics, migration idempotency |

`pytest` deselects `integration` by default, so a plain run is *not* the whole suite. CI runs both.
The integration suite always runs against the disposable test databases in the repo root's
`docker-compose.test.yml`, in CI and locally, and never against this service's own `postgres`
container, because its tests delete rows. The root `conftest.py` aborts an integration run aimed
anywhere else. Details in [`../README.md`](../README.md).

The unit tests stub at the layer boundaries above, which is the practical payoff of having them:

- **Repository tests** fake the connection and cursor, so the SQL that gets built and the rows
  handed to `COPY` stay observable — the assertions are about what was sent to the database, not
  about a mock having been called.
- **Pipeline tests** stub the `save_*` functions and swap the source registries for fakes. That
  leaves orchestration decisions — which tickers resolve, how they group, what gets skipped, what
  gets printed — testable with no database and no network.
- **Route tests** (`tests/unit/api/`) build a bare `FastAPI()` app from `api/routes.py` with
  `require_internal_secret` dependency-overridden to a no-op, and monkeypatch `jobs.get_*` —
  the same "stub one layer down" approach the pipeline tests use, so a route test asserts on
  status codes and response shape without a database or a real secret. Security itself
  (`require_internal_secret`) is tested directly as a plain function, bypassing FastAPI's
  dependency injection entirely, since it takes no state beyond its two arguments.

Progress output is treated as behaviour and asserted with `capsys`/`caplog`, on the grounds that a
run's summary is as much a part of its contract as its return value.

## Where to start reading

1. `pipeline.py` — the five flows; everything else is in service of these.
2. `jobs.py` — how configuration, a connection and a flow become one runnable operation.
3. `cli.py` — how a command becomes a job. Thin on purpose; if it is growing, something belongs
   in `jobs.py` instead.
4. One domain module end to end (`market_data/` is the most representative of a
   provider-backed one; `returns/` is the most representative of a derived one).
5. [`schema.md`](schema.md) — why the tables load the way they do.
6. `api/routes.py` and `read_flows.py` — the read side's equivalent of 2 and 3, once the write
   side above makes sense; the "Read API" section covers the design choices peculiar to it.
