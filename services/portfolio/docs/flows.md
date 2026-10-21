# portfolio flows and interfaces

Companion to [`architecture.md`](architecture.md), split out of it to keep both files under the
repo's 64KB limit. That file owns the layering and the module boundaries, and
[`cross-cutting-decisions.md`](cross-cutting-decisions.md) the service-wide decisions; this one
owns what runs: every flow and its entry points, the HTTP API, and what portfolio needs from
data-store's read API. "Above" and "below" in the sections here refer to
this file; references to sections that stayed in `architecture.md` name it.

## The flows

| Flow | Command | HTTP endpoint (task 9) | Cadence | Reads | Writes |
|---|---|---|---|---|---|
| Account setup | `account add --user <id> --broker <key> --label <label>` | `POST /accounts` | rare, manual | — | `accounts` |
| Account listing | `account list --user <id>` | `GET /accounts` | manual, on demand | `accounts`, scoped by `user_id` | — (read-only report) |
| Account delete | `account delete --user <id> --label <label>` | `DELETE /accounts?user_id=&account_label=` | manual, by the account's owner | `accounts`, scoped by `user_id` | `return_snapshots`, `position_snapshots`, `transactions` and `accounts`: every row of the account, in one transaction (404 if the label didn't resolve) |
| Broker-plugin discovery | `broker list` | `GET /brokers` | manual, on demand | `BROKER_PARSERS`, `CASHFLOW_PARSERS` (in-memory) | — (read-only report: each broker and its import kinds) |
| Ticker mapping: set | `ticker-mapping set --kind ISIN\|SYMBOL --identifier <id> [--broker <b>] --ticker <t> --user <admin id>` | `PUT /ticker-mappings` | manual, by an admin, as unmapped instruments surface | data-store's `GET /tickers/{ticker}` (the ticker must exist), then `transactions` joined to `accounts` (every account, across users, using the instrument) | `ticker_mappings` (upsert), then `position_snapshots` for each affected account |
| Ticker mapping: delete | `ticker-mapping delete --id <n> --user <admin id>` | `DELETE /ticker-mappings/{id}?updated_by=` | manual, by an admin | `transactions` joined to `accounts` (affected accounts) | `ticker_mappings` (delete), then `position_snapshots` for each affected account |
| Ticker mapping listing | `ticker-mapping list` | `GET /ticker-mappings` | on demand | `ticker_mappings` | — (read-only report) |
| Ticker list | — (CLI has none) | `GET /instruments/tickers` | on demand | `data-store`'s `GET /tickers` (`datastore_client.list_tickers`), passed through so `web` only ever talks to `portfolio` | — (read-only report) |
| Tracked tickers (ticker onboarding) | — (CLI has none) | `GET /instruments/tickers/{t}/lookup`, `POST /instruments/tracked-tickers`, `GET /instruments/tracked-tickers`, `PATCH /instruments/tracked-tickers/{t}` | on demand (web's Tickers pages) | passed through to `data-store`'s matching routes; `data-store` owns every rule (symbol, daily limit, provider), and `web` decides `enforce_limit` (false for an admin) | `data-store`'s `tracked_tickers` (via its API) |
| List unmapped instruments | `list-unmapped-instruments --user <id>` | `GET /instruments/unmapped` | manual, on demand | `transactions` joined to `accounts` (scoped by `user_id`, and to pick up each instrument's owning broker), `ticker_mappings` | — (read-only report) |
| List unmapped instruments, every user | `list-unmapped-instruments --all` | `GET /instruments/unmapped/all` | on demand (the admin page) | `transactions` joined to `accounts`, unscoped; `ticker_mappings`. Returns identifiers only, never whose they are; an ISIN appears once, with no broker | — (read-only report) |
| Manual transaction entry | `add-transaction --user <id> --account <label> --ref <key> --type <type> --datetime <iso8601> --amount <decimal> --currency <ISO4217> [--instrument <id> --instrument-kind ISIN\|SYMBOL\|TICKER] [--quantity <d>] [--price <d>] [--fx-rate <d>] [--fees <d>]` | `POST /transactions` | ad hoc | `accounts`, scoped by `user_id` | `transactions` (idempotent on `(account_id, broker_transaction_ref)`, keyed by the mandatory `--ref`) |
| Broker file import | `import-transactions --user <id> --file <path> --account <label>` | `POST /transactions/import` (multipart) | manual, on demand | export file, `accounts` scoped by `user_id` | `transactions` (idempotent on `(account_id, broker_transaction_ref)`) |
| Cash-ledger import | `import-cashflow --user <id> --file <path> --account <label>` | `POST /cashflows/import` (multipart) | manual, on demand | export file, `accounts` scoped by `user_id`, data-store's FX read API (`datastore_client`) for non-EUR rows | `transactions` (idempotent on `(account_id, broker_transaction_ref)`) |
| Transaction listing | — (CLI has none) | `GET /transactions` | on demand | `transactions`, scoped through the resolved account | — (read-only report) |
| Transaction delete | `delete-transaction --user <id> --account <label> --ref <key>` | `DELETE /transactions?user_id=&account_label=&broker_transaction_ref=` | manual, by the account's owner | `accounts`, scoped by `user_id` | `transactions` (one row deleted, 404 if none matched), then `position_snapshots` and `return_snapshots` for that account |
| Positions rebuild | `rebuild-positions --user <id> [--account <label>]` | `POST /positions/rebuild` | automatically after every transaction write that saves rows (see "Positions and returns follow every transaction write") and, for each affected account, after every ticker-mapping change | `transactions`, `ticker_mappings` | `position_snapshots` |
| Position listing | — (CLI has none) | `GET /positions` | on demand | `position_snapshots`, latest row per ticker | — (read-only report) |
| Returns rebuild | `rebuild-returns --user <id> [--account <label>]` | `POST /returns/rebuild` | automatically after every positions rebuild that follows a write or a mapping change (see "Positions and returns follow every transaction write") | `position_snapshots`, `transactions` (`DIVIDEND` rows), `ticker_mappings`, `data-store` read API | `return_snapshots` |
| Returns refresh, every user | `rebuild-returns --all` | — | every `PORTFOLIO_RETURNS_REFRESH_SECONDS` (default 6h), by the `returns-refresh-all` container (see "Returns follow new prices") | as for one user, for each user with an account | `return_snapshots` |
| Returns refresh, newly ready tickers | `refresh-ready-tickers --window-minutes N` | — | every 5 minutes with a 10-minute window, by the `returns-refresh-new-tickers` container | `data-store`'s `GET /tracked-tickers?status=ready` (`ready_at` inside the window), then `position_snapshots` joined to `accounts` for the accounts holding them | `return_snapshots`, for those accounts only |
| Return listing | — (CLI has none) | `GET /returns`, `GET /returns/latest` | on demand | `return_snapshots`, optionally filtered by ticker/date range | — (read-only report) |
| Holdings across accounts | — (CLI has none) | `GET /holdings?user_id=` | on demand (web's `/portfolio` page, PF-4) | `accounts` by `user_id`, then each account's latest `position_snapshots` and `return_snapshots` rows | — (computed on read, see "Holdings across accounts") |
| Portfolio return series | — (CLI has none) | `GET /returns/portfolio?user_id=[&start=&end=]` | on demand (web's `/portfolio` chart, PF-6) | `accounts` by `user_id`, then each account's full `return_snapshots` history | — (computed on read, see "Portfolio return series") |
| Portfolio TWR | — (CLI has none) | `GET /returns/portfolio/twr?user_id=` | on demand (ex-post risk, RK-11) | `accounts` by `user_id`, then each account's full `return_snapshots` and latest `position_snapshots`; `data-store`'s `GET /tickers/{t}`, `GET /prices/{t}` and `GET /fx/EUR/{c}` per held ticker | — |
| Ex-post risk | — (CLI has none) | `GET /risk/ex-post?user_id=&benchmark=` | on demand (web's `/risk` page, RK-12) | as for the portfolio TWR, plus `data-store`'s `GET /rates/risk-free`, and the benchmark's `GET /tickers/{t}`, `GET /prices/{t}` and, outside EUR, `GET /fx/EUR/{c}` (RK-13) | — |
| Ex-ante risk | — (CLI has none) | `POST /risk/ex-ante` | on demand (web's `/risk` page, RK-18) | `data-store`'s `GET /tickers/{t}`, `GET /prices/{t}` and `GET /fx/EUR/{c}` per asset and for the benchmark, and `GET /rates/risk-free` (RK-17); no portfolio table: web sends the mix | — (read-only: nothing is stored) |
| Labs backtest | — (CLI has none) | `POST /labs/backtest` | on demand (web's `/labs` page, LB-6) | `data-store`'s `GET /tickers/{t}`, `GET /prices/{t}` and `GET /fx/EUR/{c}` per asset and for the benchmark (RK-13); no portfolio table | — (read-only: nothing is stored) |

**No separate `brokers` table** ever materialized — `schema.md` settled on `broker` as a plain
string on `accounts`, so `account add` writes only to `accounts`, never a second table.
`account add --broker <key>` is free text, checked only for non-blank and within the column's
length (`InvalidBroker`) — it is
**not** validated against `BROKER_PARSERS` or any other fixed list of names, on purpose: account
creation and manual transaction entry must both work with zero broker-parser plugins installed.
The stored `broker` only needs to match a registered parser once `import-transactions`/
`import-cashflow` looks it up from the account -- neither command takes its own `--broker`.

**A load-bearing consequence of the entity model in [`architecture.md`](architecture.md), worth stating directly:** `rebuild-positions`
needs no price data at all — quantity and average cost are pure functions of the transaction log.
So **everything except `returns/` and the "current market value" feature can be built, tested and
shipped before `data-store`'s read API exists.** That sequencing fact changes what "blocked on the
prerequisite" actually means in practice — it's one flow, not the whole service.

**Both rebuilds are full rebuilds, same reasoning as data-store's `calculate-returns`**: a
late-arriving ticker mapping, or a corrected/added historical transaction, changes every snapshot
from that point forward, so incremental append would serve stale rows. Rebuild scope is per
account (an isolatable failure unit, mirroring data-store's per-ticker/per-group isolation).

**An unmapped instrument blocks its own rebuild, loudly** — `rebuild-positions`/`rebuild-returns`
process every other account/instrument normally, and report the blocked ones by name in the
completion summary with a `partial` status, following the same `run_result` contract and
`nothing_to_do` vs. `partial` vs. `ok` distinction data-store already established (and the
repo-wide logging rule: "nothing to do" must never look like "something failed silently").

**A known sharp edge in that blocking rule**: it's keyed by
`(instrument_identifier_kind, instrument_identifier)`, the only key `mappings.resolve` can look
up — there is no way to tell, from an unresolved identifier alone, which resolved ticker (if any)
names the same real-world instrument. If one account's transactions for a single holding are
split across a mapped identifier kind (e.g. `ISIN`) and an unmapped one for that broker (e.g.
`SYMBOL`), that ticker's snapshot is built only from the mapped rows — silently a partial view,
not the blocked-and-untouched outcome described above. The fix lives in the ticker mappings (map
every identifier kind a broker actually emits for an instrument), not in `rebuild_positions` itself — there's no reliable way for the code to infer
that two different identifier strings name the same instrument without the mapping saying so for
both.

**A rebuild's own failure isolation** — the per-`(account, ticker)` transaction boundary above is
also where a driver-level failure (a constraint violation, a dropped connection; anything that
isn't a `PortfolioError`, since repositories never raise those) is caught, logged with its
traceback, and reported in `failed_pairs`/`pairs_failed` rather than propagating and aborting
every account still left to rebuild. This is deliberately narrower than a general "catch
everything" policy: it only wraps the two repository calls inside the per-pair loop
(`replace_snapshots`, `delete_snapshots`), the same boundary `shared.db.connection.transaction`
already rolls back on any exception — which is what leaves the connection usable for the next
pair instead of stuck in Postgres's aborted-transaction state. Every other flow in this file keeps
the plain "an uncaught exception means nothing committed" contract; `rebuild_positions` earns
this extra handling specifically because it is the one flow that is *not* single-transaction, so
without it a failure partway through would silently discard the accurate count of what had
already, durably, succeeded.

**Three decisions task 7 had to settle, none of which were pinned down before it started:**

- **Cost basis is sourced from `native_amount`/`eur_amount`**, the broker's own frozen cash-flow
  figures (`schema.md`'s "immutable source of truth"), not `quantity * price`. The consequence
  worth keeping in mind: `average_cost_eur` needs no FX arithmetic in `positions/calculator.py` at
  all — it never divides by `fx_rate` — because `eur_amount` already froze that conversion at
  transaction time. One corollary that isn't obvious from the columns alone: `average_cost_eur` is
  a *blend of each purchase's own frozen `eur_amount`*, not derivable from `average_cost_native` by
  any single `fx_rate` — see `schema.md`'s note on the same instrument held in two currencies.
- **Only `BUY` and `SELL` move a position.** Every other `transaction_type` is a cash event —
  `validate()` doesn't even guarantee a `DIVIDEND`/`FEE`/`ADJUSTMENT` row carries a `quantity` or
  `price` — so none of them touch `position_snapshots` here. **Correction against this
  document's own earlier wording**: only `DIVIDEND` actually went on to feed `returns/` (task 8,
  `return_snapshots.dividends_received_eur`) — `FEE` and `ADJUSTMENT` still feed nothing anywhere
  in the service, the same open gap as the missing `realized_gains` table (`schema.md`).
- **A ledger anomaly warns but still computes, rather than blocking.** Two cases:
  `position_snapshots.native_currency`'s single-currency-per-instrument-per-account assumption
  being violated within one account, and a running quantity going negative. Both are logged
  (`positions_with_anomalies` in the run result) and the snapshot is still written; only an
  unmapped instrument flips the run to `partial`. Rationale: unlike an unmapped instrument, there
  is no action a user can take from the CLI to resolve either anomaly, so blocking the rebuild
  would only make the portfolio look emptier without giving them a way to fix it.

**Pruning a ticker `rebuild-positions` no longer produces** (e.g. a ticker mapping was re-pointed
to a different `canonical_ticker`) is handled, but
conservatively: `pipeline.rebuild_positions` only deletes a stale `(account, ticker)` pair when
that account currently has **zero** blocked instruments. A blocked instrument's old
`canonical_ticker` — from before its mapping was removed — is indistinguishable from a genuinely
stale one at rebuild time, and "blocked means don't touch, not delete" means the ambiguous case
must never delete. An account with any blocked instrument simply skips pruning for that run.

`rebuild-returns` follows: a ticker with return rows but no position snapshots left loses its
rows (`tickers_removed`), so it inherits the rule above rather than needing its own blocked
check. Before 2026-10-10 it never pruned, so a re-pointed mapping's old ticker kept showing
returns on the account and home pages. See `schema.md`'s loading strategy for
`return_snapshots`.

### Positions and returns follow every transaction write

`run_add_transaction`, `run_import_transactions` and `run_import_cashflow` each end by calling
`run_rebuild_positions` and then `run_rebuild_returns` for the one account they just wrote to
(`jobs._rebuild_after_write`), and report their outcomes as `positions_rebuild` and
`returns_rebuild` alongside their own `status`.

`run_delete_transaction` does the same after a delete, through the same isolated helper
(`jobs._rebuild_derived_isolated`), for the same reason: without it the deleted row would keep
showing in the portfolio view until someone ran a CLI command. The positions rebuild prunes a
ticker the delete left with no transactions, and the returns rebuild then drops that ticker's
return rows and any rows from before a window start that moved later (see "Pruning a ticker"
above and `schema.md`'s loading strategy for `return_snapshots`). A miss (no such reference in
the account) raises `TransactionNotFound` before anything is rebuilt. **Known limitation:** in an account with
a blocked (unmapped) instrument the positions rebuild skips pruning altogether, so a ticker
whose only BUY was deleted keeps its last position, and with it its returns, until that
instrument is mapped.

`run_delete_account` is the exception: it rebuilds nothing. `pipeline.delete_account` deletes the
account's return snapshots, position snapshots and transactions, and then the account itself, in
that order and inside one transaction. Every derived row it could have rebuilt goes with the
account, and no other account's rows are derived from it. The order is forced by the foreign keys
to `accounts`, none of which cascade. Doing it in code rather than with `ON DELETE CASCADE` kept
the DDL unchanged, and keeps an account delete an explicit act that a stray `DELETE FROM accounts`
can't trigger. The response reports `transactions_deleted`, so a caller can say what went with the
account. **Known edge:** a write to the same account that commits while the delete runs (an
import, or the rebuild that follows one) makes the delete fail with a foreign-key violation and
roll back whole, with nothing removed. Retrying the delete succeeds. Locking the account row first
would serialize the two instead, which isn't worth it for something a single user does by hand.

**Why:** `GET /positions` reads only `position_snapshots`, and before this nothing but an explicit
`rebuild-positions` ever wrote it — so a transaction entered through the web UI was durably saved,
reported as saved, and then simply absent from the portfolio view, with the only remedy a CLI
command a web user has no access to. That looked exactly like data loss. A positions rebuild is
cheap enough to run inline: it's one account, needs no price data (see above), and is a full,
idempotent replacement, so running it on every write can never leave a stale or duplicated row.

**Where, and why there:** in `jobs.py`, not in `web` and not in `pipeline.py`. Every entry point —
CLI, HTTP API, and through it `web` — reaches a write via these three jobs, so putting it here
covers all of them with one call site, and a future write path that goes through a job gets it
for free. `pipeline.py` was rejected because each pipeline flow is one unit of work over one
connection with one transaction contract; composing two of them is orchestration, which is
`jobs.py`'s job. Having `web` call `POST /positions/rebuild` after each write was rejected because
it would fix only the web UI and leave the CLI and any other API caller with the same gap.

**Returns second, and only on top of good positions.** `return_snapshots` is computed from
`position_snapshots`, so the returns rebuild runs after the positions one and is skipped
(`returns_rebuild: skipped`) when the positions rebuild failed: returns over stale positions
would be wrong, not just late. It's also skipped when data-store isn't configured
(`DATASTORE_API_URL`/`INTERNAL_API_SECRET`), with a WARNING naming the `rebuild-returns` command to
run once it is, so a CLI-only setup without data-store still saves transactions.

*Why returns joined the write path after first being kept off it:* this section used to leave
returns out on purpose, because `rebuild-returns` makes HTTP calls to data-store and that put a
cross-service failure mode on the write. In practice that left every new transaction's returns
chart empty until someone ran a CLI command a web user can't reach — the same "saved but
invisible" problem positions had. The failure mode is contained the same way positions' is: the
call happens after the write has committed, and any failure is reported, never raised.

**Skipped when the write saved nothing** (`positions_rebuild`/`returns_rebuild: skipped`): an
idempotent resubmit leaves the transaction log unchanged, so the snapshots derived from it are
unchanged too.

**Failure isolation:** the rebuild opens its own connection only after the write's connection has
closed and its transaction committed, so nothing the rebuild does can roll a saved transaction
back. A rebuild that *raises* (a dropped connection, say) is
therefore caught, logged with its traceback, and reported as `positions_rebuild: failed` rather
than propagated — propagating would turn a durably saved write into an error response and invite
the caller to act on a false signal. The returns rebuild is isolated the same way, separately: it
reports `returns_rebuild: failed` without touching `positions_rebuild`. These follow-on steps are
the one place a write job catches a non-domain exception, deliberately never the write itself.

**The write's own `status` never reflects the rebuild.** An unmapped instrument blocks its own
ticker's rebuild (`positions_rebuild: partial`), but the transaction was still saved, and
`status`/HTTP 201 must keep meaning exactly that.

**Known limitations:**

- **`web` surfaces `positions_rebuild`/`returns_rebuild` (UI-7)** as a one-line outcome on its
  confirmation banners, and lists the account's unmapped instruments (from
  `GET /instruments/unmapped`) on the holdings page. That list is per user and carries the broker
  but not the account, so `web` can only filter it by broker. An exact per-account list would need
  a new endpoint here; see `services/web/docs/architecture.md`.
- **Accounts whose transactions predate this change** have no position snapshots until their next
  write or a manual `rebuild-positions`; nothing backfills them. (Returns do get backfilled, by
  the scheduled refresh below, for any account that has positions.)
- **The rebuilds are synchronous**, so a write's response time includes them — and, since
  returns, a few data-store HTTP calls for that one account's tickers. Fine at today's volumes; a
  large broker import into a long-lived account is where to look first if that stops being true.

### Returns follow new prices

Every day data-store's `daily_pipeline` DAG ingests new closes, which moves every holder's return
whether or not they touched their account. The `returns-refresh-all` container
(`docker-compose.stakehold-net.yml`) runs `rebuild-returns --all` in a loop, every
`PORTFOLIO_RETURNS_REFRESH_SECONDS` (default six hours). `jobs.run_rebuild_returns_all` runs
`run_rebuild_returns` for each user with an account, isolated per user: one raising is logged and
counted in `users_failed`, and the rest still run. Blocked tickers and failed pairs are summed and
make the run `partial`. A run when nothing changed rewrites the same rows.

*Why portfolio polls instead of data-store calling it when the DAG finishes:* portfolio depends on
data-store and never the reverse, and Airflow isn't even on `stakehold-net`. A DAG step would
need Airflow on that network, portfolio's inbound secret in data-store's config, and data-store
knowing portfolio exists — a new dependency in the wrong direction, to save at most one polling
interval of lag on daily closes. *Rejected: compute returns on read* — see the
[cross-cutting decision](cross-cutting-decisions.md) on materialization. *Known cost:* every run
rebuilds every user's returns in full, including users whose holdings have no new prices. Fine at
this scale; the place to start if it isn't is skipping users whose latest snapshot is already at
data-store's latest price date.

**A ticker that has just finished loading gets its own, faster sweep.** A user who adds a ticker
can record the holding while data-store is still loading it (see "Manual entry stores a
`data-store` ticker"). Its returns are blocked until prices land, and waiting up to six hours
after that for `rebuild-returns --all` would make the loading message a lie.

- The `returns-refresh-new-tickers` container runs `refresh-ready-tickers` every five minutes. It
  asks data-store for tickers marked `ready` in the last ten minutes, and rebuilds only the accounts
  whose positions hold one, across all users.
- **It's stateless on purpose**: a time window, not a "last seen" checkpoint, so it needs no
  table of its own. The window is twice the interval, so every ticker falls inside at least one
  run. A ticker caught twice is rebuilt twice, which is a harmless re-upsert. If the container is
  down for longer than the window, the six-hourly refresh still catches the ticker up.
- It polls for the same reason the six-hourly refresh does: data-store never calls portfolio.

### Positions follow every ticker-mapping change

A mapping is global, so saving or deleting one can change positions in any user's accounts.
`jobs.run_set_ticker_mapping`/`run_delete_ticker_mapping` therefore find every account whose
transactions use that instrument (`transactions.list_accounts_using_instrument` — every broker for
an ISIN, the mapping's own broker for a SYMBOL) in the same connection as the write, close it, and
then rebuild each of those accounts' positions and returns with the same isolation the
transaction-write rebuild uses (`_rebuild_derived_isolated`): one account's rebuild failing is
logged and counted (`rebuilds_failed` for positions, `returns_rebuilds_failed` for returns),
never raised, and never stops the others. The response's `status` describes the mapping change
alone; the counts sit beside it. The CLI exits non-zero when any rebuild failed, so a
retry-worthy outcome isn't a clean exit.

*Rejected: rebuild every account in the deployment* — simplest, but its cost grows with every
user, for a change that typically touches a handful of accounts. *Rejected: leave the rebuild to
the next transaction write* — that's exactly the stale-positions bug the transaction-write rebuild
exists to fix.

**Deleting a mapping doesn't remove the positions it produced.** The rebuild after a delete finds
the instrument blocked, and blocked means don't touch (see "Pruning" above), so the old ticker's
snapshots stay until the instrument is mapped again. Re-pointing doesn't have this problem, unless
the account holds some other unmapped instrument (pruning is skipped for the whole account then).

### Manual transaction entry

`add-transaction`'s `--ref` is **mandatory**, unlike a broker import where `broker_transaction_ref`
comes from the file. That's what makes manual entry idempotent by construction: re-running the
same command with the same `--ref` hits `(account_id, broker_transaction_ref)`'s `ON CONFLICT ...
DO NOTHING` and reports `nothing_to_do`, exactly like re-importing an overlapping file. A caller
who wants a one-off, throwaway entry still has to choose a ref (e.g. a UUID) — there's no
auto-generated fallback, because a generated ref would make every invocation a *new* row instead of
a safe re-run, silently defeating the idempotency the mandatory flag exists to give.

The EUR side of a transaction is computed locally, not fetched. `--currency EUR` requires no rate:
`eur_amount = native_amount`. Any other currency requires `--fx-rate`, read as **native currency
units per 1 EUR** (e.g. `--currency USD --fx-rate 1.0850` means EUR 1 buys USD 1.085), so
`eur_amount = native_amount / fx_rate`, rounded half-up to `eur_amount`'s own four decimal places
(`transactions/base.py`'s `to_eur`). The stored `fx_rate_source` is `"manual"` — a fourth value
added to the vocabulary `schema.md` originally documented as `broker_reported` / `companion_row` /
`datastore_series`, none of which fit a rate the user typed in; see `schema.md`'s amendment.

**Known limitation, partly revisited at task 14**: a non-EUR manual entry still has no fallback to
`data-store`'s FX series — if the caller doesn't know the day's rate, `add-transaction` simply fails
with `InvalidTransaction` rather than looking one up. `datastore_client.get_fx_rate` now exists
(built for `import-cashflow` below), so this is technically reachable, but wiring
`add-transaction` to it wasn't part of what task 14 was asked to do — left as an explicit, easy
follow-up rather than done as a drive-by.

**Manual entry stores a `data-store` ticker, not an ISIN or symbol.** The web form offers
`data-store`'s own ticker list (`GET /instruments/tickers`) and always submits
`instrument_identifier_kind = 'TICKER'`. `pipeline.add_manual_transaction` asks `data-store`
whether the ticker exists (`get_ticker_info`) before writing anything, and raises `UnknownTicker`
(422) if not. "Exists" includes a ticker `data-store` has accepted but is still loading
(`status` `pending` or `backfilling`): a user who has just added a ticker can record the holding
straight away, and its returns wait for prices the same way any blocked ticker's do. A ticker
whose loading `failed` is refused with its own message. `set_ticker_mapping` applies the same
rule (`pipeline._require_usable_ticker`). `TickerMappings.resolve` returns a `TICKER` identifier as-is, so positions,
returns and the unmapped-instruments report all handle it without a mapping.

*Why:* ticker mappings exist to translate what a broker export reports into the ticker
`data-store` prices. For a transaction the user types in, there's nothing to translate: they're
choosing the instrument, so they can choose it in `data-store`'s own terms. Asking them for an
identifier plus a kind, and then a separate mapping, was two extra steps, one of them a
hand-edited config file. It also let a typo through unnoticed: a manually entered `IWDA.AM`
(`data-store` only has `IWDA.AS`) was saved, then blocked at every rebuild with nothing in the UI
saying why. The check at save time is what makes skipping the mapping safe: a `TICKER` row is
only ever written once `data-store` has confirmed it.

*Rejected:* looking the ticker up from an ISIN automatically (for example through a Yahoo
search). That's the inference the ticker-mapping design rules out; a wrong match would attach the
wrong price series to a position without anyone noticing.

**A manual BUY/SELL's amount is derived, not typed.** `--amount` (and the form's Amount field) is
optional for a BUY or SELL: `pipeline.add_manual_transaction` computes it as
`-(quantity * price) - fees`, rounded half-up to four places. One formula covers both, since
quantity is already signed: a BUY comes out negative (cash out, including fees), a SELL positive
(cash in, net of fees). Every other type has no quantity or price to derive from, so its amount
is still required.

*Why:* cost basis comes from `native_amount` (task 7's decision above), and for a trade the user
types in, that figure is just their own arithmetic on numbers they already entered. Asking for it
separately invited a mismatch nothing caught: a manual BUY of 5 × 128.65 was saved with an amount
of `10`, which would have priced the position at €2 a share. A typed amount is still accepted,
since a broker's confirmation can differ from price × quantity + fees by a rounding cent and its
figure is the one to keep, but only within €0.01 of the derived value; anything further is
rejected with the expected figure in the message. That tolerance also enforces the sign, which
the positions calculator itself doesn't check (it takes the absolute value).

*Scope:* manual entry only. A broker import keeps the broker's own figure untouched, including
transaction taxes and bundled fees a manual entry would put in `--fees`.

**Consequence: a `TICKER` entry needs `data-store`.** `add-transaction` with a `TICKER` requires
`DATASTORE_API_URL`/`INTERNAL_API_SECRET`, as `import-cashflow` does, and fails with `SettingsError`
without them. Manual entry of an ISIN or symbol (still possible from the CLI) needs no
`data-store` at all.

### Cash-ledger imports needing a live FX lookup

Some brokers' trade export carries no EUR-equivalent at all for any row — unlike a format where a
broker-reported EUR column or an already-EUR-denominated base currency lets `BrokerParser.parse`
stay pure, no I/O beyond the file itself. A non-EUR row in that kind of export needs a live
FX-rate lookup mid-parse to get a `eur_amount`. Widening `BrokerParser.parse()` to carry a
`datastore_client` parameter for every implementation — including every parser that never needs
one — was rejected in favor of a second, parallel registry: `CashflowParser`/`CASHFLOW_PARSERS`,
whose `parse()` takes a plain `resolve_fx_rate` callable instead (see [`broker-import.md`](broker-import.md)).
`pipeline.import_cashflow` resolves rates through `datastore_client` and hands that callable to
whatever `CashflowParser` is registered for the resolved account's `broker` — the parser
implementation stays pure and testable against a fake callable, no HTTP client required.
`import-cashflow --user <id> --file <path> --account <label>` is its own CLI command, not a
file-type-sniffing branch inside `import-transactions`.

**FX resolution contract**: for a non-EUR row, `resolve_fx_rate(currency, on_date)` calls
`datastore_client.get_fx_rate("EUR", currency, on_date)`, which looks back up to 7 days for the
most recent point at or before `on_date` (covering a weekend/holiday with no traded rate) and
returns `None` if nothing is found in that window. `None` is not silently tolerated — a
`CashflowParser` implementation is expected to raise (`BrokerFileError`, by convention) rather
than persist a row with a gap. The stored `fx_rate_source` for this path is `"datastore_series"`.

The one installed plugin's own row-classification rules — which description strings map to which
canonical type, which rows are the broker's own internal housekeeping to skip, the locale caveat
for a non-English export — are documented in that plugin's own `docs/architecture.md`, not here,
for the same reason as the extension points in [`broker-import.md`](broker-import.md): those specifics are exactly what stays out of
this repo.

### Holdings across accounts

Everything a user holds, summed per `canonical_ticker` across all of their accounts: 120 IWDA in
one account and 20 in another is one row of 140, with a per-account breakdown.
`holdings/flows.list_holdings` lists the user's accounts through `accounts/repository`, reads
each one's latest position and return rows by its own id, and hands them to
`holdings/aggregator.aggregate_holdings`. The response is

    {"holdings": [Holding, ...], "totals": Totals | null}

`holdings` is sorted by ticker, each row with its `accounts` in label order. `totals` sums every
latest return row, closed tickers included, so it matches web's home page; it is null when no
account has a return row yet. A user with no accounts gets `{"holdings": [], "totals": null}`,
not a 404. The rules, and why the summing happens here, are in `architecture.md`'s "Holdings
across accounts are summed here, on read".

### Portfolio return series

The whole portfolio's returns over time, across every account a user holds.
`holdings/flows.list_portfolio_returns` lists the user's accounts through `accounts/repository`,
reads each one's full `return_snapshots` history, and hands it all to `returns/performance.py`
at once. The response is

    {"series": [{price_date, market_value_eur, cost_basis_eur, dividends_received_eur,
                 total_return_eur, total_return_pct}, ...]}

- **`series`** has one point per date any (account, ticker) has a row. A holding with no row
  that day holds its own last row, and before its first row it contributes nothing. The euros are
  summed, `total_return_pct` is `total_return_eur / cost_basis_eur` from those sums (null when
  the cost is 0).
- **`start` and `end` crop `series` only**, after it is computed, so a holding last priced
  before `start` still carries its value into the range.

A user with no accounts, or no rows yet, gets `{"series": []}`, not a 404. Why it is computed
here, and why the forward-fill is in Python, is in `architecture.md`'s "The portfolio series is
summed here, on read".

### Portfolio TWR

The whole portfolio's daily time-weighted return, invested positions only (RK-10): the series
ex-post risk is measured on. `twr/flows.compute_portfolio_twr` lists the user's accounts through
`accounts/repository`, reads each one's full `return_snapshots` history and latest
`position_snapshots`, fetches every held ticker's prices and FX from data-store (through
`eur_returns/fetch.py`, from the ticker's first row to today), converts them to EUR returns
(`eur_returns/calculator.py`), and hands it all to `twr/calculator.py`. The response is

    {"base_date", "points": [{price_date, twr_return, twr_growth}, ...], "n_gaps",
     "n_excluded", "excluded": [{"ticker", "status", "reason"}, ...]}

- **`twr_return`** on date t is `Σ w_(i,t−1) · r_eur,i,t`: each ticker's share of the EUR market
  value at the previous date, times its EUR total return. A buy or a sell only changes the next
  date's weights, and a dividend counts on its ex-date. It is null on a gap: a held ticker's
  return was NULL that day. `n_gaps` counts them.
- **`twr_growth`** is 1 at `base_date`, the first date with holdings, and is flat across a gap.
- **A date with nothing held at the previous date has no point**, so after a full exit the
  series resumes at the first date after re-entry. That isn't a gap.
- **`excluded`** names each held ticker left out, by ticker, and the rest are renormalised to
  100%: `not_tracked` ("not tracked"), `no_history` ("no history yet"), `no_fx_rate` ("no FX
  series for USD", for example) or `failed` ("couldn't reach data-store"; the exception is
  logged, not returned).

A user with no accounts, or no rows yet, gets an empty series, not a 404. The flow logs one line
per ticker (included, or left out and why); the job logs a start line and a summary, which says
whether there were no rows at all or every holding was left out. Why it is holdings-based, and
what that gives up, is in `architecture.md`'s `twr/` row.

### Ex-post risk and Labs backtest

Moved to [`risk-flows.md`](risk-flows.md) (RK-13), with every later risk flow.

## HTTP API

**Built at task 9.** `src/portfolio/api/{app,routes,security,models}.py` — FastAPI routes that
are argument-parsing-plus-a-call-into-`jobs.py`, the same shape `cli.py` has, per the flow table
above. Mirrors `auth`'s shape more than `data-store`'s: an injectable `Settings` argument to
`create_app()` (so tests build an app with a known secret without touching the environment), and
`X-Internal-Secret` rather than `Authorization: Bearer` — the header `web`'s `HttpAuthClient`
already sends, so a future `PortfolioClient` in `web` is a copy of that module, not a new shape.
`app.state.settings` (set once in `create_app()`) is threaded through every route's own
`Depends(get_settings)` into the matching `jobs.py` call's `settings` keyword, rather than each
`run_*`/`get_*` re-deriving it via its own `load_settings()` — the same `Settings` instance a test
built into `create_app()` is therefore what every route, not just `/health`, actually queries
against. `cli.py` never passes `settings`, so each job's own `load_settings()` fallback keeps the
CLI's behaviour unchanged.
`/docs`, `/redoc`, `/openapi.json` are disabled, `data-store`'s reasoning: they mount as plain
Starlette routes no router-level dependency covers, and this is a service-to-service API with no
`/docs`, `/redoc`, `/openapi.json` are disabled, `data-store`'s reasoning: they mount as plain
Starlette routes no router-level dependency covers, and this is a service-to-service API with no
interactive audience.

**`account_label` and `user_id` are always a query parameter or a request-body field, never a
path segment.** A label is user-chosen free text up to 100 characters and may contain `/`, which
no amount of path-segment escaping survives cleanly through a proxy; `user_id` is likewise
explicit, matching `cli.py`'s `--user` and this document's existing "`user_id` is trusted input"
stance (see [`architecture.md`](architecture.md)'s "Conceptual entity model") — the shared secret proves a legitimate internal
caller, not a specific user.

**Error mapping moved the `except PortfolioError` out of `jobs.py`, into each entry point.**
Before this task, every `run_*` job caught `PortfolioError` and collapsed it into a generic
`{"status": "partial", "rows_saved": 0}` result, with the exception's message only logged — an
HTTP layer reading that dict alone could not tell "account not found" from "unparseable broker
file" from "data-store unreachable." `jobs.py` now lets `PortfolioError` propagate; `cli.py`
catches it in `main()` (alongside the pre-existing `SettingsError` clause) and prints a one-line
stderr message; the API maps each subclass to its own status code via `app.py`'s exception
handlers, `auth`'s pattern of app-level handlers rather than per-route `try`/`except`:

| Exception | Status | Why |
|---|---|---|
| `AccountNotFound` | 404 | `--account`/`account_label` didn't resolve for this user — the isolation chokepoint |
| `TickerMappingNotFound` | 404 | a mapping delete for an id that's already gone |
| `TransactionNotFound` | 404 | a transaction delete whose reference isn't in the (resolved) account, most likely already deleted |
| `AccountLabelConflict` | 409 | a label re-used under a different broker |
| `InvalidBroker`, `InvalidTransaction`, `BrokerFileError`, `BrokerParserNotFound`, `UnknownTicker`, `InvalidTickerMapping` | 422 | a caller-supplied value failed validation or parsing |
| `TickerMappingError`, `BrokerPluginError` | 500 | a server-side data/plugin problem, not a caller mistake (`TickerMappingError` now means a stored transaction carries an identifier kind `resolve` doesn't know) |
| `DataStoreApiError` | 502 | the upstream `data-store` call failed |
| `TickerRequestRefused` | `data-store`'s own (404, 409, 422, 429, 503) | `data-store` refused a tracked-ticker request for a reason the user must be told: not on the provider, can't be held, over the daily limit, provider down, or an admin change it won't make. Kept apart from `DataStoreApiError` so `web` can tell these apart from the status code |
| any other `PortfolioError`, `SettingsError` | 500 | fails closed rather than leaking a raw traceback |

**One practical consequence: `partial` now means only genuine partial success.** The two rebuild
endpoints (`POST /positions/rebuild`, `POST /returns/rebuild`) can still report `status: partial`
via `pipeline.rebuild_positions`/`rebuild_returns`'s own per-pair `blocked`/`failed_pairs`
handling (unchanged by this task) — a real half-succeeded run, returned as **200**, with the
blocked/failed detail in the body, not an error status, since the caller needs that list to know
which instruments to map. Every write endpoint, rebuild routes included, goes through the same
`routes._write_status_code`: `ok` is **201**, anything else (`nothing_to_do`, or a rebuild's own
`partial`) is **200** — a domain error on any of them reaches the API as an exception instead, per
the table above.

**Broker-file imports take a multipart `UploadFile`.** `import-transactions`/`import-cashflow`
both take a local filesystem `Path` in `jobs.py` — unchanged by this task, since the CLI still
needs that — so the two upload routes (`_save_upload_to_tempfile` in `routes.py`) stream the
request body to a size-capped (20 MB) temp file in chunks, call the job with that path, and
delete the file in a `finally`, on both the success and the error path. A caller-supplied
server-side path was deliberately not offered as an alternative to a real upload — that would be
an arbitrary-file-read primitive sitting behind one shared secret.

**Returns are served per ticker**, not aggregated across a portfolio. `GET /returns` takes an
optional `ticker`/`start`/`end` filter and returns one series per matching `(account, ticker)`
pair; there is no endpoint that sums market value or return across every ticker an account holds
into one portfolio-level series. This is deliberate, not an oversight: different tickers can sit
on different trading calendars, so summing across them is a real design question — how to handle
a date one ticker priced and another didn't — not a serialization detail, and it's left to task
12 (the view UI), where the chart's actual shape will settle the question.

`GET /returns/portfolio` is the one exception, and answers that question for itself across every
account a user holds: a ticker with no row on a date holds its last value (the forward-fill web's
chart already used), keyed on (account, ticker) so one ticker in two accounts stays two series
until they are summed. It returns the summed EUR figures; see "Portfolio return series" above.

`GET /holdings` sums across accounts rather than across dates: one row per ticker from each
account's latest rows, so no calendar question arises. See "Holdings across accounts" above.

`GET /returns/portfolio/twr` uses the same union calendar and keying, with one difference: a
closed holding counts only through its last row. Like the backtest, a data-store failure doesn't
make it a 502: the ticker is listed in `excluded`. See "Portfolio TWR" above.

`GET /risk/ex-post` measures that series, so it inherits its calendar and its exclusions. A
failed risk-free fetch isn't a 502 either: the figures that need the rate are greyed. See
[`risk-flows.md`](risk-flows.md)'s "Ex-post risk".

`POST /risk/ex-ante` takes a mix, as the backtest does, and isolates failures the same way: each
asset's status says why it was left out, and a failed risk-free or benchmark fetch greys the
figures that need it. See [`risk-flows.md`](risk-flows.md)'s "Ex-ante risk".

`POST /labs/backtest` answers the calendar question the way `GET /returns/portfolio` does: each
asset carries its last value over days it didn't trade. Unlike every other route here, an
upstream failure doesn't make it a 502. Each asset's data-store calls are isolated, and a failure
is reported in that asset's `status`. See [`risk-flows.md`](risk-flows.md)'s "Labs backtest".

**Known limitations, same category as `data-store`'s own read API:**

- **One connection per request.** `jobs.py`'s `run_*`/`get_*` functions open and close their own
  connection, exactly as they already do for the CLI — `psycopg_pool` (as `auth`'s API already
  uses) is the intended fix if request volume ever makes per-request connection setup a
  bottleneck. `/health` pays this cost too, independent of any real traffic: Docker's own
  healthcheck (`docker-compose.yml`, 10s interval) opens and closes a fresh connection every tick
  for the container's whole lifetime — the same fix would absorb this cost as well.
- **`GET /transactions` is unpaginated and untyped by date range** — it returns an account's
  entire transaction log every time, the same shape `transactions_repository.list_by_account`
  already had before this task; a large account's history is not yet a concern this API solves.
- **The shared secret is caller-agnostic.** `X-Internal-Secret` proves "a legitimate internal
  caller" (i.e., `web`), not cryptographically that a specific `user_id` came from a session that
  service actually authenticated — the same accepted gap `auth/docs/architecture.md` documents
  for its own shared-secret model, and the same reasoning behind treating `user_id` as trusted
  input ([`architecture.md`](architecture.md)'s "Conceptual entity model").

## Cross-service prerequisite: the data-store read API

**This section describes what portfolio needs from it; `data-store`'s own `docs/read-api.md` owns
the design and is authoritative on the current shape.** Built as task 3
of the walking-skeleton roadmap (`docs/roadmap-archive/walking-skeleton.md`). `http_client.py` now implements every operation below.

`returns/` (and any future "current market value of a position" feature) needs, conceptually,
three read operations against `data-store`, all of which now exist on both data-store's and
portfolio's sides:

1. **Price series for a canonical ticker** over a date range — `GET /prices/{ticker}`, backed by
   `market_returns`. Returns `split_adjusted_close`, `future_split_factor`, and the four return
   columns (`price_return`, `dividend_return`, `simple_return`, `log_return`), nullable together
   on a ticker's first stored date. **Built at task 8** as `DataStoreClient.get_price_series(
   ticker, start, end)`; `http_client.py` carries `split_adjusted_close` and, since **LB-2**,
   `simple_return` into `PricePoint` (NULL stays `None`). `returns/calculator.py` ignores
   `simple_return` — it computes its own `unrealized_return_eur`/`total_return_eur` locally — and
   Labs' backtest chains it. The other three return columns aren't carried.
2. **FX rate series for a currency pair** over a date range, in the *same shape* as a price
   series (a list of `{date, value}` points) — `GET /fx/{base}/{quote}`. **Correction to an
   earlier assumption here**: this is *not* modeled as another yfinance-style ticker (e.g.
   `EURUSD=X`) reusing `market_data/`'s ingestion machinery. `data-store` deliberately kept FX in
   its own `fx_rates` table (no OHLC, dividends, or splits to carry), and the API instead derives
   any requested pair — direct, inverse, or triangulated through `config/currencies.yaml`'s
   configured base currency — from whatever `fx_rates` actually stores. The response shape still
   satisfies the "same shape as a price series" intent (`{date, rate}` points); only the
   ingestion-reuse assumption was wrong. **`get_fx_rate` built at task 14** — a single-date lookup
   with a 7-day look-back for the nearest earlier point, still used unchanged by `import_cashflow`.
   **`get_fx_rate_series(base, quote, start, end)` built at task 8** as the range-returning
   sibling `returns/calculator.py` actually needs — one HTTP call per `(account, ticker)` pair
   needing conversion, rather than one per priced day.
3. **Ticker existence/validity check** — `GET /tickers/{ticker}`, reporting `exists` (ingested at
   all) and `has_returns` (whether `calculate-returns` has run for it) separately, plus the
   covered date range, and (since data-store's LB-1) the ticker's `currency` and `fx_currency`,
   which `TickerInfo` carries since **LB-2** so a backtest can choose each asset's FX pair from
   `fx_currency`. **Built at task 8** as `DataStoreClient.get_ticker_info(ticker)`; unlike
   the other two, this endpoint never 404s (`exists: false` is itself a normal `200` answer), so
   `http_client.py`'s implementation has no 404 special-case. `rebuild_returns` calls this first,
   per ticker, cached for the whole run: a ticker data-store doesn't track (or hasn't had
   `calculate-returns` run for it) blocks only that `(account, ticker)` pair — loudly, in the run's
   `blocked` list — rather than failing at the price-series fetch that follows it.

**The risk-free series** (**RK-4**, tasks-risk-thread.md) is a fourth read, for the risk
measures rather than `returns/`: `GET /rates/risk-free`, data-store's €STR/EONIA splice (RK-3;
its `read-api.md` owns the contract). `DataStoreClient.get_risk_free_series(start, end)` returns
the published points (`RiskFreePoint`: date, `Decimal` rate in annualised %, the series it came
from) with the series-wide `first_date` R4 needs. It returns `None` when data-store has nothing
stored (its `first_date: null`), so a caller has to report "no risk-free data" rather than
compute with rf = 0. It doesn't forward-fill: `risk/risk_free.py` does, after the caller converts
the points to its float `RatePoint`. Its `source` defaults to `ecb` on data-store's side.

All four require `Authorization: Bearer <INTERNAL_API_SECRET>` — the same shared-secret model
`auth/docs/architecture.md` describes — and `source` is a query parameter defaulting to
`yfinance` on the first three (`ecb` on the risk-free one), since `source` is part of the primary
key on data-store's side; `http_client.py` relies on those defaults rather than passing `source`
explicitly, matching `get_fx_rate`'s existing convention.

`datastore_client/base.py` defines the `DataStoreClient` Protocol portfolio's domain code depends
on; `http_client.py` is the implementation that calls the now-built API, every method.
Because the rest of the domain (`positions/`, `transactions/`, `broker_import/`,
`ticker_mapping/`, `returns/`) depends only on the Protocol, not the HTTP implementation, a local
stub/fixture-backed implementation of `DataStoreClient` remains enough to build and test
everything — `import-cashflow` (its own tests, and `tests/integration/test_cashflow_import.py`)
and `rebuild-returns` (its own tests, and `tests/integration/test_rebuild_returns.py`) alike —
without a live data-store instance.
