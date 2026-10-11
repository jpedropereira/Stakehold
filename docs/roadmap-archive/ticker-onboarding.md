# Ticker-onboarding roadmap (archived)

Completed 2026-10, tasks TK-0 to TK-10. This is the ticker-onboarding chunk's `tasks.md`, moved here
when `tasks.md` was repurposed for the next roadmap chunk (portfolio view). The content is verbatim
except for relative links, adjusted for the new location. Its Deferred list is carried forward into
the current `tasks.md`.

---

# Tasks

Roadmap for the ticker-onboarding chunk: letting users and admins add a Yahoo Finance ticker that
`data-store` doesn't price yet, instead of an operator editing `tracked_tickers.yaml` on the server.
A symbol is checked against Yahoo before it can be added, its full history is backfilled in the
background, and every page that shows it says plainly whether it is loading, ready or failed.
The previous chunks are archived in [`docs/roadmap-archive/`](./):
[walking skeleton](walking-skeleton.md) and
[UI/UX](ui-ux.md).

**Task IDs are prefixed `TK-`** so they can't be confused with the walking-skeleton's bare "task N"
references or the UI chunk's `UI-N`.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency (or, for TK-1, a sign-off).

## Agreed requirements

- Users add tickers directly, within a daily limit (default 10, rolling 24 h). Admins have no limit.
- A symbol Yahoo doesn't know can't be added. "Yahoo didn't answer" is reported as "try again
  later", never as "doesn't exist".
- Adding a ticker backfills its full history (yfinance `max`, 1960 onward wherever Yahoo has it).
- `tracked_tickers.yaml` stays as the platform's default list and is loaded automatically.
- Delisted securities are unsupported for now, as a documented limitation.

## Constraints that apply to every task

- **Schema is the user's.** `tracked_tickers` (TK-1) is built only after explicit sign-off, per
  `.claude/rules/data-schema.md`, and documented in `services/data-store/docs/schema.md` in the
  same change.
- **Call path stays web → portfolio → data-store.** web never calls data-store directly. Admin
  checks stay in web (`require_admin`), the same trust model the ticker-mapping routes use.
- **The daily ingest prices only `ready` tracked tickers.** If it landed a rolling window for a
  pending ticker first, backfill's resume rule ("skip any ticker that already has data") would then
  skip that ticker's history entirely.
- **Tests first**, per `.claude/rules/testing.md`. Each service's `docs/architecture.md` is updated
  in the same change whenever a task is architectural, per `.claude/rules/architecture.md`.
- **Brand**: status chips use `--gold` and neutral tokens, never forest or oxblood. Copy follows
  chapter IV of the charter.

## Status table

| ID | Task | Depends on | Status |
|----|------|-------------|--------|
| TK-0 | [yfinance behaviour spike](#tk-0-yfinance-behaviour-spike) | — | done |
| TK-1 | [`tracked_tickers` table & YAML sync](#tk-1-tracked_tickers-table--yaml-sync) | schema sign-off | done |
| TK-2 | [Priced flows read the table](#tk-2-priced-flows-read-the-table) | TK-1 | done |
| TK-3 | [Yahoo ticker lookup](#tk-3-yahoo-ticker-lookup) | TK-0 | done |
| TK-4 | [FX universe follows ticker currencies](#tk-4-fx-universe-follows-ticker-currencies) | TK-1 | done |
| TK-5 | [Onboarding flow & DAG](#tk-5-onboarding-flow--dag) | TK-2, TK-4 | done |
| TK-6 | [data-store API for tracked tickers](#tk-6-data-store-api-for-tracked-tickers) | TK-3, TK-5 | done |
| TK-7 | [portfolio pass-through & relaxed checks](#tk-7-portfolio-pass-through--relaxed-checks) | TK-6 | done |
| TK-8 | [web: add-ticker and tickers pages](#tk-8-web-add-ticker-and-tickers-pages) | TK-7 | done |
| TK-9 | [web: loading state on the portfolio page](#tk-9-web-loading-state-on-the-portfolio-page) | TK-8 | done |
| TK-10 | [portfolio: returns catch up when a ticker is ready](#tk-10-portfolio-returns-catch-up-when-a-ticker-is-ready) | TK-7 | done |

## Tasks

### TK-0. yfinance behaviour spike

**Status:** done

The lookup's three outcomes (found / not found / couldn't check) rest on how yfinance actually
responds, which must be observed rather than assumed. Make real calls and record what comes back
for: a valid equity, ETF, crypto and mutual fund; an unknown symbol; a delisted symbol; a
pence-quoted London listing (`GBp`); an index (`^GSPC`); and, if it can be provoked, a throttled
request. For each: does `history(period="5d")` raise or return empty, what does
`history_metadata` contain (name, currency, exchange, instrument type), and which exception types
appear.

Output: findings recorded in `services/data-store/docs/architecture.md`, and any change they force
on TK-3's design noted in its subsection. No production code.

As found: findings are in data-store's `architecture.md`, "How yfinance answers a symbol lookup
(verified)". The one that changes the design: an unknown symbol raises a 404 `HTTPError`, which is
an `OSError` and so counts as *transient* under `yfinance_retry._is_transient`. TK-3 changed
accordingly.

### TK-1. `tracked_tickers` table & YAML sync

**Status:** done (schema signed off 2026-10-10)

- New `tracked_tickers` table, PK `(ticker, source)`. Columns: `display_name`, `asset_type`,
  `currency`, `exchange`, `origin` (`config`/`request`), `status`
  (`pending`/`backfilling`/`ready`/`failed`), `enabled`, `attempts`, `next_attempt_at`,
  `last_error`, `requested_by`, `requested_at`, `ready_at`, `updated_at`. Indexes on
  `(requested_by, requested_at)` and a partial index on in-flight statuses. Full proposal in the
  approved plan; final shape as signed off.
- `tracked_tickers/repository.py` and `sync_config_tickers`: inserts missing YAML tickers as
  `origin='config', status='pending'`, applies YAML `enabled: false`, and warns (never deletes)
  for a config row no longer in the YAML.
- CLI: `data-store sync-tracked-tickers`.

Files: `db/schema.sql`, `tracked_tickers/repository.py`, `jobs.py`, `cli.py`; `docs/schema.md`,
`docs/architecture.md`.

As built:
- `ticker_onboarding.py` was created here rather than in TK-5, to hold `sync_config_tickers`
  outside the 42 KB `pipeline.py`. TK-5 adds the onboarding run to it.
- **The YAML is authoritative for every ticker it names.** It owns `display_name`, `asset_type`,
  `enabled` and `origin`. Naming a ticker a user requested first turns it into a config row,
  keeping its `requested_by`. The sync never touches onboarding state.
- An over-long YAML ticker is skipped with a warning and makes the run `partial`.
- No `CHECK` constraints on `origin`/`status`. Their values are named once in the repository,
  matching how the service already validates `asset_type`.
- The sync is a manual command until TK-2 runs it before every priced flow.

### TK-2. Priced flows read the table

**Status:** done

`jobs._run_priced_flow` syncs the YAML, then reads tracked tickers from the table instead of the
YAML: ingest takes `enabled AND status='ready'`, backfill takes every enabled row. Fund-holdings
pricing is unchanged.

Files: `jobs.py`, `tracked_tickers/repository.py`; `docs/architecture.md` (resolution diagram,
configuration section).

As built:
- The table-to-`TickerConfig` conversion is `ticker_onboarding.tracked_ticker_configs`, so the
  flows in `pipeline.py` are unchanged and still take `TickerConfig`s.
- **A ticker added to the YAML is no longer priced daily until it is `ready`.** Existing YAML
  tickers start as `pending` too. TK-5's onboarding marks them `ready` within one run, since their
  history is already stored and backfill skips them. Until TK-5 runs, the daily ingest prices
  fund holdings only. This is why TK-2 and TK-5 must ship together.

### TK-3. Yahoo ticker lookup

**Status:** done

New `tracked_tickers/base.py` (`TickerLookupSource` Protocol) and `yfinance_source.py`. One
`history(period="1mo")` plus its metadata gives one of three outcomes. The window is a month
rather than 5 days so a briefly suspended listing still counts as found.

- `found`: rows came back. Store `history_metadata["symbol"]` as the canonical spelling. The
  display name is `longName`, falling back to `shortName`.
- `not_found`: a 404 `HTTPError` or any `YFTickerMissingError`. This is checked **before**
  `_is_transient`, which would otherwise call a 404 transient (TK-0).
- `unavailable`: `YFRateLimitError`, or anything else `_is_transient` matches.

Instrument types map `EQUITY`→`equity`, `ETF`→`etf`, `CRYPTOCURRENCY`→`crypto`,
`MUTUALFUND`→`fund`, `FUTURE`→`commodity`. `INDEX`, `CURRENCY` and anything unknown are rejected
as "can't be held". A symbol whitelist (`^[A-Z0-9.\-=^]{1,20}$`, after uppercasing), a 10 s timeout, and a 15-minute
in-process cache of `found`/`not_found` (never `unavailable`).

Files: `tracked_tickers/{base,yfinance_source}.py`, `config_loader.py` (`fund` asset type);
`docs/architecture.md`.

As built:
- **A fourth outcome, `unsupported`**: the symbol has prices but is an index or a currency pair.
  It's split from `not_found` so the user is told why it can't be added.
- `normalize_symbol`, `LookupCache` and `lookup_symbol` live in `ticker_onboarding.py`, the
  orchestration side. The source only classifies.
- Checked against live Yahoo: `asml.as`→found as `ASML.AS`, `VOD.L`→found in `GBp`,
  `VFIAX`→found as `fund`, `^GSPC` and `EURUSD=X`→unsupported, `NOTAREALTICKER123`, `SIVB` and
  `TWTR`→not_found.

### TK-4. FX universe follows ticker currencies

**Status:** done

The FX universe becomes `currencies.yaml`'s enabled codes plus the major-unit currency of every
enabled tracked ticker (`GBp`→`GBP`, `ZAc`→`ZAR`, `ILA`→`ILS`). Applies to `ingest_fx_rates`,
`backfill_fx_rates` and the read API's `CurrencyNotTracked` check.

Files: `ticker_onboarding.py`, `jobs.py`; `docs/architecture.md`, `docs/schema.md` (`fx_rates`
universe), `README.md`.

As built:
- The widening (`currencies_with_tracked_tickers`) runs in the job layer, so `pipeline.py` and
  `read_flows.py` are unchanged. They still take a plain `CurrenciesConfig`.
- A currency the YAML explicitly disables stays disabled, with a warning naming the tickers it
  leaves unconvertible.
- A config ticker's `currency` is empty until onboarding fills it (TK-5), so its currency joins
  the universe only after its first onboarding run.

### TK-5. Onboarding flow & DAG

**Status:** done

New `ticker_onboarding.py` (kept out of the 42 KB `pipeline.py`) and a `process-ticker-requests`
CLI. Each run: syncs the YAML; returns rows stuck in `backfilling` for over an hour to `pending`;
then for each due `pending` row sets `backfilling`, runs `backfill_market_data(only_tickers=…)`,
backfills FX for an uncovered currency, runs `calculate_returns(only_tickers=…)`, and sets `ready`.
A failure increments `attempts` with backoff (about 10 min × 2^attempts); the 3rd failure sets
`failed`. Returns the standard run-result contract. New DAG `ticker_onboarding`, every 5 minutes,
`max_active_runs=1`, using `dags/common.py`'s `check_run(expect_rows=False)`.

Files: `ticker_onboarding.py`, `jobs.py`, `cli.py`, `dags/ticker_onboarding.py`;
`docs/architecture.md` (flows table, deployment).

As built:
- **Success is judged by what landed.** `backfill_market_data` absorbs a per-ticker fetch failure
  as "no data", so `ready` requires stored prices, the FX pair (for a non-base currency) and
  successful returns.
- **Only a ticker given up on makes the run `partial`.** A retry still to come is a warning, so a
  passing Yahoo blip doesn't turn the DAG red.
- A config row has no currency, so onboarding looks it up first and stores it with the exchange.
  Failing to is a warning, not a failed attempt.
- At most 50 tickers per run, with a 2 s pause after each provider fetch.
- Run for real against the test database with live Yahoo:
  - `ASML.AS` (config row, no currency): looked up as EUR, then history back to 1998.
  - `NPN.JO` (`ZAc`): 6,851 price rows, plus EUR/ZAR rates added automatically.
  - An unknown symbol: scheduled for a retry.
- The new DAG is paused when the dev Airflow first sees it (`dags_are_paused_at_creation`), so it
  can't run against an image built before this change.

### TK-6. data-store API for tracked tickers

**Status:** done

- `GET /tickers/{t}/lookup`: already priced (with status), available on Yahoo (with metadata),
  not found, or unavailable.
- `POST /tracked-tickers` `{ticker, requested_by, enforce_limit}`: 201 added, 200 already
  tracked, 404 not on Yahoo, 422 unsupported type, 429 over the daily limit, 503 Yahoo
  unavailable. Re-validates server-side rather than trusting an earlier lookup.
- `GET /tracked-tickers?requested_by=&status=` and `PATCH /tracked-tickers/{t}`
  (enable/disable/retry). Enabling or disabling a config row through the API is refused (409),
  since the next YAML sync would undo it (TK-1). That is edited in the YAML instead.
- `TickerInfo` gains `status`.
- New setting `DATA_STORE_TICKER_REQUESTS_PER_DAY` (default 10).

Files: `api/{routes,models}.py`, `jobs.py`, `read_flows.py`, `settings.py`; `docs/architecture.md`
(Read API section, which stops being read-only), `README.md`.

As built:
- The request flows (`lookup_ticker`, `add_tracked_ticker`, `list_tracked`, `update_tracked`) live
  in `ticker_onboarding.py` and raise named exceptions. The routes map each to its status code in
  one table.
- **The lookup reports `outcome`:** `tracked` (with `status`), `available`, `unsupported`,
  `not_found` or `unavailable`.
- **`POST` answers 201 or 200 with the row plus `created`.** Adding an already-tracked ticker is
  free and isn't counted.
- **The limit is checked before the provider is asked.** The provider is asked again on add
  rather than trusting an earlier lookup.
- `GET /tracked-tickers` also takes repeated `ticker` filters, for TK-9's status lookups.
- `DATA_STORE_TICKER_REQUESTS_PER_DAY` is added to `.env.example`.

### TK-7. portfolio pass-through & relaxed checks

**Status:** done

`DataStoreClient` methods and pass-through endpoints for TK-6's routes. Manual entry and
mapping-save existence checks accept "exists, or tracked and `pending`/`backfilling`", so a user
can record a holding while its prices load; `failed` is still refused with a clear message.

Files: `datastore_client/{base,http_client}.py`, `api/{routes,models}.py`, `pipeline.py`,
`jobs.py`; `docs/architecture.md`.

As built:
- **Routes:** `GET /instruments/tickers/{t}/lookup`, `POST`/`GET /instruments/tracked-tickers`,
  `PATCH /instruments/tracked-tickers/{t}`.
- **`enforce_limit` is required, with no default**, so the caller always decides whether the
  daily limit applies.
- **A new error, `TickerRequestRefused`, keeps data-store's status code** (404, 409, 422, 429,
  503), so web can tell "not found" from "over the limit". Anything else stays a 502
  `DataStoreApiError`.
- **The ticker is quoted into the URL path.** That keeps a typed `a/b` from reaching another
  route.
- `pipeline._require_usable_ticker` accepts `exists`, or `pending`/`backfilling`. `failed` gets
  its own message pointing to an admin retry.

### TK-8. web: add-ticker and tickers pages

**Status:** done

- `/tickers/new`: enter a symbol → look up → confirm name, exchange and currency → add. A
  not-found message says delisted securities may not be available.
- `/tickers`: the tickers you added, with status. Admins see every ticker, plus disable and retry.
- "Not in the list? Add a ticker" links from manual entry and the admin mappings page.
- Progressive enhancement and CSP rules as in the UI chunk.

Files: `portfolio_client/*`, new `api/tickers.py`, new `ticker_status.py`, `api/routes.py`
(error leads only), `api/app.py`, new `templates/tickers*.html`, `base.html`,
`transactions_new.html`, `admin_mappings.html`; `docs/architecture.md`, `docs/ui-architecture.md`.

As built:
- **The pages live in `api/tickers.py`**, their own router, because `routes.py` is at 60 KB. The
  status labels live in `ticker_status.py`, shared with the portfolio page.
- **A "Tickers" nav link for every user.** Users see the tickers they added, and admins see every
  ticker with Retry (for a failed one) and Disable/Enable (for a user-added one).
- **The lookup is a GET form**, so it works without JS. Adding is a separate CSRF-checked POST
  that sends `enforce_limit = not user.is_admin`.
- **A minor-unit currency (`GBp`, `ZAc`, `ILA`) is warned about before adding.** Its values will
  read 100 times too high until the deferred rescaling exists.
- No new CSS: the pages reuse the existing card, field, table, badge and banner styles.

### TK-9. web: loading state on the portfolio page

**Status:** done

Holdings whose ticker isn't `ready` show "Prices loading" or "Prices couldn't be loaded", never a
blank or a zero.

Files: `api/routes.py`, `ticker_status.py`, `portfolio.html`; `docs/architecture.md`,
`docs/ui-architecture.md`.

As built: the page asks for statuses only for holdings with no return row, in one extra call.
They read "Prices loading" (`pending`/`backfilling`) or "Prices couldn't be loaded" (`failed`).
Anything else, or a failed status call, keeps "Waiting for prices", so UI-7's decision not to
split untracked from not-priced-yet still holds.

### TK-10. portfolio: returns catch up when a ticker is ready

**Status:** done

Without this, returns for a newly ready ticker wait for the 6-hourly `rebuild-returns --all`. The
`returns-refresh` loop also runs a cheap sweep every few minutes: tickers marked `ready` since its
last check, rebuilding only the accounts that hold them.

Files: `jobs.py`, `cli.py`, `pipeline.py`, `positions/repository.py`,
`docker-compose.stakehold-net.yml`; `docs/architecture.md`, `docs/smoke-test.md`.

As built:
- **A new command, `portfolio refresh-ready-tickers --window-minutes N`**, run by a new
  `returns-refresh-ready` container every 5 minutes with a 10-minute window.
- **Stateless, with no portfolio table:** it lists data-store's `ready` tickers and keeps those
  whose `ready_at` is inside the window. A ticker caught twice is rebuilt twice, which is a
  harmless re-upsert.
- It rebuilds only the accounts whose `position_snapshots` hold one of those tickers, across
  users. Each account is isolated.

## Known limitations

- **Delisted securities** can't be added (Yahoo usually drops them).
- **Pence-quoted listings** (`GBp`, `ZAc`, `ILA`): FX coverage uses the major currency, but nothing
  in the system rescales prices quoted in minor units. This gap predates this chunk.
- **The lookup cache** is per API process, which is fine with one uvicorn worker.

## Deferred

Carried over from the UI/UX chunk, each needing a new `portfolio` endpoint (and possibly a schema
discussion per `.claude/rules/data-schema.md`):

- Editing or deleting a transaction.
- Renaming or deleting an account.
- A server-side cross-account return-over-time series (UI-11 shows the current snapshot only).
- Pagination or filtering of transactions, if `GET /transactions` doesn't already support it.
- A list of the currencies `data-store` tracks (its `currencies.yaml`), so manual entry can
  suggest exactly those (UI-9 suggests a fixed list instead).
- Unmapped instruments per account, not per user (UI-7 filters by broker, so two accounts at one
  broker share a list).
- Rescaling prices quoted in minor currency units (`GBp`, `ZAc`, `ILA`).
