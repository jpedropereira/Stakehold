# portfolio schema

**Signed off and implemented.** The DDL lives at `src/portfolio/db/schema.sql` and is applied by
`portfolio db init`, guarded the same way as `data-store`'s (`IF NOT EXISTS` / `DO $$...$$` blocks
so it stays safe to rerun on any database). Per
[`../../../.claude/rules/data-schema.md`](../../../.claude/rules/data-schema.md), this document was
the proposal signed off on before that file was written, and is now the description of what exists.
Keep it in lockstep with any future schema change, the same discipline
[`data-store`'s schema.md](../../data-store/docs/schema.md) follows. For *why the service is laid
out this way*, see [`architecture.md`](architecture.md), which this document turns from a
conceptual entity model into concrete tables.

Five tables: `accounts` (who owns what — `user_id` is a plain UUID referencing `auth`, not a local
`users` table), `transactions` (the ledger), `position_snapshots` /
`return_snapshots` (derived, materialized, rebuildable), and `ticker_mappings` (the global,
admin-managed broker-instrument → `data-store`-ticker mapping). The four user-owned tables are
under row-level security, and `ticker_mappings` is not (see "Row-level security" below).

## Users — not a table here

Originally proposed as a local `BIGSERIAL`-keyed bootstrap table (`id`, `email`, `display_name`)
standing in for a dedicated identity service, and signed off on as such in an earlier pass of this
document. **Removed here, not an oversight**: per
[`auth/docs/architecture.md`](../../auth/docs/architecture.md), `auth` is the sole owner and issuer
of user identity from the start, so there is nothing for portfolio to bootstrap or later hand off.
`accounts.user_id` stores the UUID `auth` issues at signup directly, as a trusted plain column — see
`accounts` below.

## `accounts`

One row per brokerage account a user has told portfolio about.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | Surrogate PK — what `transactions` and the snapshot tables reference. |
| `user_id` | `UUID NOT NULL` | The id `auth` issues at signup. Trusted plain column, not a foreign key — there's no local `users` table to reference, and portfolio never opens a connection to `auth`'s database. Not validated against `auth` at write time; see the open items below. |
| `broker` | `VARCHAR(50) NOT NULL` | e.g. `'acme'`, `'globex'` — matches `broker_import`'s registry keys. Validated at the app layer, not a DB `CHECK`, the same convention data-store uses for `raw_market_data.asset_type`. |
| `account_label` | `VARCHAR(100) NOT NULL` | User-chosen, e.g. `'acme-main'` — what `--account` on the CLI actually takes. |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

Indexes: `(user_id)` for "list my accounts"; `UNIQUE (user_id, account_label)` so a label is a
stable, human-chosen handle scoped to its owner, not required to be globally unique.

**Not stored: the broker's own account identifier** (e.g. Globex's masked `U***57423`). Matching an
imported file to an account is always done via the caller-supplied `--account`, never by parsing a
broker-side id out of the file — Acme's export doesn't even have an equivalent column — so
storing it would be display-only data with no reader.

**No separate `brokers` table.** `broker` is a plain, app-validated string, the same choice
data-store made for `etf_holdings.fund_key` ("no separate `funds` reference table — easy to add
later if richer per-fund metadata needs to live in the DB"). The same applies here: adding a
`brokers` table is a cheap follow-up if broker-level metadata (display name, supported file
formats, docs links) ever needs to live in the database rather than in code.

**Loading strategy**: single-row insert via `account add --user <id> --broker <key> --label
<label>`, where `<id>` is the UUID `auth` issued at signup — pasted in by the caller, not looked up
or validated against `auth` (see open items). `ON CONFLICT (user_id, account_label) DO NOTHING`:
re-running with the same broker is a safe no-op; re-running with a *different* broker for a label
already taken is rejected rather than silently ignored, since the two calls disagree about a fact
that can't both be true.

**Deleting**: `account delete --user <id> --label <label>` (`DELETE /accounts`) removes the
account with everything recorded in it: its `return_snapshots`, `position_snapshots` and
`transactions`, then the `accounts` row, in that order and in one transaction. The foreign keys to
`accounts` have no `ON DELETE`, which is why the order is explicit; no DDL changed for this. It's a
hard delete, like a transaction delete, and the label is free to use again afterwards. See
[`flows.md`](flows.md)'s "Positions and returns follow every transaction write" for why no
rebuild follows.

## `ticker_mappings`

One row per mapping from a broker-reported instrument to the canonical ticker `data-store` prices.
**Global, not per user**: an ISIN or a broker's symbol names the same instrument whoever holds it,
so one mapping serves every user, and only an admin changes it (the check lives in `web` — see
`architecture.md`'s "Ticker mappings live in the database, managed by an admin"). This is close to
the `ticker_links` table an early pass of this document signed off on; it spent a while as
`configs/ticker_mappings.yaml` instead, which this table replaces.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL` | PK. What the admin page deletes by. |
| `identifier_kind` | `VARCHAR(10) NOT NULL` | `'ISIN'` \| `'SYMBOL'`, the kinds broker exports report. `'TICKER'` never gets a row: it resolves to itself. |
| `identifier` | `VARCHAR(50) NOT NULL` | As the broker reports it — same width as `transactions.instrument_identifier`, which it's matched against exactly. |
| `broker` | `VARCHAR(50)` | `NULL` for an ISIN (one ISIN mapping covers every broker), required for a SYMBOL (a bare symbol isn't globally unambiguous). Matched against `accounts.broker`. Enforced by the CHECK below. |
| `canonical_ticker` | `VARCHAR(20) NOT NULL` | Same spelling/width as `position_snapshots.canonical_ticker` and `data-store`'s `raw_market_data.ticker`. **No FK** — data-store is another database; the ticker is checked against data-store's API when the mapping is saved instead. |
| `updated_by` | `UUID NOT NULL` | The `auth` user id of the admin who last set it. A trusted plain column, like `accounts.user_id`. A delete isn't recorded here (the row is gone); `jobs.run_delete_ticker_mapping` logs who did it. |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | `updated_at` moves when a mapping is re-pointed. |

Constraints and indexes:

- `CHECK ((identifier_kind = 'ISIN' AND broker IS NULL) OR (identifier_kind = 'SYMBOL' AND broker
  IS NOT NULL))` — the ISIN-global / symbol-per-broker rule, which also rejects any other kind.
  `pipeline.set_ticker_mapping` checks the same rule first, so an admin gets a sentence rather than
  a constraint violation; the CHECK is the backstop for anything that writes around it.
- `UNIQUE (identifier) WHERE identifier_kind = 'ISIN'` and `UNIQUE (broker, identifier) WHERE
  identifier_kind = 'SYMBOL'` — two partial indexes rather than one over `(identifier_kind,
  identifier, broker)`, because Postgres treats two `NULL` brokers as distinct, so a single index
  would let the same ISIN be mapped twice.

**Loading strategy**: single-row upsert from the admin page or `portfolio ticker-mapping set`
(`INSERT ... ON CONFLICT` against whichever partial index the kind uses, so saving a mapping for an
already-mapped instrument re-points it in place), and single-row `DELETE` by `id`. Each change is
followed by a positions rebuild of every account whose transactions use that instrument. A rebuild
reads the whole table once into memory (`repository.load_all`) and resolves every row against
that.

The practical consequence for this document: `position_snapshots` and `return_snapshots` below
reference a `canonical_ticker` string directly rather than a `ticker_link_id` foreign key, since
there's no database row left to point at.

## `transactions`

The ledger. One row per broker-reported line item or manually entered transaction.

**Rows are added, edited or deleted, one at a time, by the account's owner.** This used to be
"the immutable ledger", with no delete path at all; then rows could be deleted but not edited, so
a correction was a delete plus a new row. Deleting is allowed one row at a time
(`portfolio delete-transaction`, `DELETE /transactions`), or every row at once when the whole
account is deleted (see `accounts`' "Deleting" above), and since the reference is then free
again, the row can be re-entered correctly. Editing in place (`portfolio edit-transaction`,
`PUT /transactions`, TA-1) saves that round trip: it rewrites every column but the key. A row is
addressed by `(account_id, broker_transaction_ref)`, the same unique key imports are idempotent
on, so an edit can't change either: an edited imported row still makes a re-import of its file
skip it. What an edit leaves alone keeps how it was recorded: an unchanged instrument keeps its
`instrument_identifier_kind`, and an unchanged rate in an unchanged currency keeps its
`fx_rate_source`. A changed rate is `'manual'`, and a changed instrument takes the kind sent
(`'TICKER'` from web). Neither edits nor deletes leave a trace of what the row was. No DDL
changed for either. The snapshots derived from an edited or deleted row are rebuilt straight
after (see [`flows.md`](flows.md)'s "Positions and returns follow every transaction write").

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `account_id` | `BIGINT NOT NULL REFERENCES accounts(id)` | |
| `broker_transaction_ref` | `VARCHAR(128) NOT NULL` | **Corrected at task 6, against a real export**: Acme's `Order ID` is *not* row-unique on its own — a partial fill of one order produces multiple rows sharing it — so the stored value is `<order_id>:<12-char content hash>:<occurrence index>`. For Globex, which carries no broker-issued id at all, it's `<12-char content hash>:<occurrence index>`, hashed over (date, symbol, type, quantity, price, price currency, gross amount, commission, net amount). See [`broker-import.md`](broker-import.md) for both parsers' exact ref construction. |
| `transaction_type` | `VARCHAR(20) NOT NULL` | `BUY` \| `SELL` \| `DEPOSIT` \| `WITHDRAWAL` \| `DIVIDEND` \| `FX` \| `FEE` \| `ADJUSTMENT`. App-layer validated, not a DB `CHECK` — an unrecognized broker transaction type is meant to be a parser-level error (per `architecture.md`), not something that reaches this table at all. |
| `trade_datetime` | `TIMESTAMPTZ NOT NULL` | |
| `instrument_identifier` | `VARCHAR(50)` | `NULL` for cash-only rows (deposits, adjustments). |
| `instrument_identifier_kind` | `VARCHAR(10)` | `'ISIN'` \| `'SYMBOL'` \| `'TICKER'`, `NULL` alongside `instrument_identifier`. `'TICKER'` means the identifier already is a `data-store` ticker (the `canonical_ticker` spelling): it's what manual entry stores, picked from `data-store`'s own list and checked against it when saved, so it resolves to itself and never needs a ticker mapping. `'ISIN'`/`'SYMBOL'` are what broker exports report, and do. The allowed set lives in `transactions/base.py`'s `IDENTIFIER_KINDS`, not a CHECK constraint, so adding `'TICKER'` needed no DDL. |
| `quantity` | `NUMERIC(20,6)` | Signed: `+` buy, `-` sell. `NULL` for cash-only rows. Six fractional digits to accommodate fractional-share brokers. |
| `price` | `NUMERIC(18,6)` | Per unit, native currency. `NULL` for cash-only rows. |
| `native_currency` | `VARCHAR(10) NOT NULL` | ISO 4217, exactly as the broker reports it — same column width as `etf_holdings.currency`. |
| `native_amount` | `NUMERIC(20,4) NOT NULL` | Signed cash-flow, the broker's own figure — the immutable source of truth (see `architecture.md`'s currency decision). For a manually entered BUY/SELL it's derived as `-(quantity * price) - fees_native` unless given, and a given one must agree with that to within 0.01 (see [`flows.md`](flows.md)'s "Manual transaction entry"). |
| `eur_amount` | `NUMERIC(20,4) NOT NULL` | Signed, frozen at transaction time — never recomputed later even if an FX source is subsequently revised. |
| `fx_rate` | `NUMERIC(18,8)` | Nullable — absent when `native_currency = 'EUR'`, required otherwise. |
| `fx_rate_source` | `VARCHAR(20)` | `'broker_reported'` \| `'companion_row'` \| `'datastore_series'` \| `'manual'`, nullable alongside `fx_rate`. **`'manual'` added at task 5** for a rate the user typed in on `add-transaction` — none of the three broker-derived sources fit one, and leaving `fx_rate_source` `NULL` for a manual rate would collide with `NULL`'s other meaning ("EUR, no rate needed"). App-layer vocabulary only, no DDL change — the column was already `VARCHAR(20)` with no `CHECK` constraint. **`'companion_row'` turned out unused at task 6**: it was reserved for matching Globex's `Forex Trade Component` rows to their parent trade, but a real Globex export shows `Gross Amount`/`Net Amount` are already EUR-denominated, so both parsers only ever write `'broker_reported'` — kept in the vocabulary rather than removed, in case a future broker genuinely needs it. **`'datastore_series'` got its first real writer at task 14**: `broker_import/acme_cashflow.py` (Acme's cash-ledger import) resolves a non-EUR row's rate via `datastore_client.get_fx_rate` — the daily FX series read from `data-store` this value was always meant to name, realized well before `returns/` (task 8), which is where it was originally expected to first appear. |
| `fees_native` | `NUMERIC(18,4)` | Nullable. |
| `imported_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | When this row was written, not the trade date. |

Indexes:

- `UNIQUE (account_id, broker_transaction_ref)` — scoped by **account**. Globex's hash-based ref is
  derived from content (date, symbol, type, amounts) that two different accounts could plausibly
  share by coincidence (same-day, same symbol, same size trade), unlike Acme's genuinely-global
  UUID `Order ID`. Scoping by account is the conservative choice that's correct for both.

  **No `broker` column here, and none in this key.** An earlier draft denormalized
  `accounts.broker` onto every transaction row and keyed on all three columns, to spare
  broker-scoped queries a join. Dropped at sign-off: an account has exactly one broker, so `broker`
  is functionally dependent on `account_id` — the three-column key constrains nothing the
  two-column one doesn't, and the column's only remaining effect would be to let a row disagree
  with its own account about who the broker is. Cross-account, per-broker queries join `accounts`,
  which is the one place the answer actually lives.
- `(account_id, trade_datetime)` — per-account, time-ordered reads.
- A **partial** index, `(instrument_identifier_kind, instrument_identifier) WHERE
  instrument_identifier IS NOT NULL` — this is what powers `list-unmapped-instruments`'s read
  (`transactions/repository.py`'s `list_distinct_instruments`, task 6): find distinct `(kind,
  identifier)` pairs here, joined to `accounts` for the owning `broker` (needed because SYMBOL
  mappings are broker-scoped — see `architecture.md`'s TickerLink key-scoping note — and for the
  `user_id` isolation scoping itself), then diff the resulting triples in application code through
  the same `TickerMappings.resolve` a rebuild uses. Not a DB anti-join against `ticker_mappings`,
  even though one is now possible: going through `resolve` keeps "unmapped" meaning exactly what
  blocks a rebuild (a `TICKER` row, for one, is never unmapped), in one place. The same index
  serves the admin page's cross-user version (`list_distinct_instruments_all`) and the lookup of
  which accounts a mapping change affects (`list_accounts_using_instrument`).

**No foreign key to `ticker_mappings`.** A transaction must be importable *before* its instrument
is mapped — that's a first-class, visible state, not an error — so `instrument_identifier`/`kind`
are stored exactly as reported and resolved against the mappings at rebuild time, not resolved once
and cached on the row. Caching the resolved `canonical_ticker` here would go stale silently the
moment a mapping is re-pointed.

**Loading strategy: insert with `ON CONFLICT (account_id, broker_transaction_ref) DO NOTHING` —
deliberately not a true upsert.** This is the opposite choice from `raw_market_data`'s
upsert-and-overwrite, for a reason specific to this table: `raw_market_data` is a *cache* of what a
provider currently says, where restatement is normal and overwriting is correct; `transactions` is
an **audit trail**, and a broker "correcting" a past transaction should arrive as a new, explicit
row (an `ADJUSTMENT`), not silently rewrite history underneath an already-computed position or
return. Re-importing a file whose date range overlaps a previous import is therefore a safe no-op
for the rows already seen, not a resync.

## `position_snapshots`

Derived, materialized, rebuildable — never hand-edited.

| Column | Type | Notes |
|---|---|---|
| `account_id` | `BIGINT NOT NULL REFERENCES accounts(id)` | Part of PK. |
| `canonical_ticker` | `VARCHAR(20) NOT NULL` | Part of PK. Same spelling/width as `data-store`'s `raw_market_data.ticker` — deliberately **no FK**, the same way `raw_market_data.ticker`/`market_returns.ticker` have no FK to any ticker-reference table in `data-store` either. Resolved from `transactions.instrument_identifier`/`kind` via `ticker_mappings` at rebuild time. A snapshot can only exist once an instrument is mapped — an unmapped instrument's position is exactly what `rebuild-positions` reports as blocked, not a row with an empty ticker here. |
| `as_of_date` | `DATE NOT NULL` | Part of PK. Event-driven grain — one row per date a transaction actually changed this position. "Position as of date D" is read as the latest row with `as_of_date <= D`, not a row that must exist for every calendar date. |
| `quantity` | `NUMERIC(20,6) NOT NULL` | Running total after this date's transactions. |
| `average_cost_native` | `NUMERIC(18,6) NOT NULL` | Blended average cost per unit, in the instrument's trading currency. |
| `native_currency` | `VARCHAR(10) NOT NULL` | Assumes one trading currency per instrument **per account** — violated only if the same account itself trades one instrument in two currencies, which `positions/calculator.py` (task 7) detects and reports as an anomaly (logged, `positions_with_anomalies` in the run result) but still computes rather than blocking. The same instrument held in *two different accounts*, each in its own currency, is ordinary and fully supported — each account replays independently, keyed separately by this table's own `account_id`, and nothing here spans accounts to notice or warn about it. |
| `average_cost_eur` | `NUMERIC(18,6) NOT NULL` | Blended average cost per unit, in EUR — specifically, a blend of each purchase's own frozen `transactions.eur_amount`, not `average_cost_native` converted by any single `fx_rate`. Two purchases at different `fx_rate`s leave this column and `average_cost_native` unrelated by any single conversion factor: `average_cost_native` blends native-currency amounts, `average_cost_eur` blends already-converted EUR amounts recorded at each purchase's own rate. A portfolio-wide total for one instrument across several accounts (possibly in different currencies) is `sum(cost_basis_eur)` — summing `average_cost_native` across accounts is meaningless once currencies differ. |
| `cost_basis_eur` | `NUMERIC(20,4) NOT NULL` | `quantity * average_cost_eur`, computed at full precision from the running (unrounded) totals and quantized once at this column's own precision — not the rounded product of the already-rounded `quantity`/`average_cost_eur` values above. Stored rather than recomputed by every reader — denormalized the same way `market_returns.split_adjusted_close` is denormalized from `raw_market_data`. Historical, not re-translated: unaffected by FX movement between purchase and now, since `transactions.eur_amount` (its ultimate source) is frozen at transaction time and never revised (`architecture.md`'s currency decision) — a change here only ever comes from a rebuild. |
| `calculated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

`PRIMARY KEY (account_id, canonical_ticker, as_of_date)`. Its own index already serves "latest
snapshot ≤ D" as a range scan (`WHERE account_id = ? AND canonical_ticker = ? AND as_of_date <= ?
ORDER BY as_of_date DESC LIMIT 1`) — no secondary index needed.

**Loading strategy: delete-then-insert per `(account_id, canonical_ticker)`, not upsert.** Unlike
`market_returns` — a fixed daily grid where a rebuild only ever changes the *values* at dates that
already exist — the event-driven dates here can themselves change on rebuild: a corrected
historical transaction can add, remove, or shift which dates count as position-changing events. So
this is a full snapshot replacement, the same shape as `etf_holdings`'s delete-then-insert, just
keyed by `(account_id, canonical_ticker)` instead of `(fund_key, as_of_date)`.

**Each `(account_id, canonical_ticker)`'s delete-then-insert runs inside one transaction**, so a
concurrent reader — `rebuild-returns`, or a future HTTP API — never observes that key with zero rows;
it sees either the pre-rebuild snapshot set or the post-rebuild one, never a transient gap. Rebuild
scope is still per account (per [`flows.md`](flows.md)'s "Both rebuilds are full rebuilds" note), so
different accounts' rebuilds aren't serialized against each other — only the delete+insert for a
single `(account_id, canonical_ticker)` is atomic, not the whole rebuild run.

**No realized-gains table yet.** A `SELL` under average-cost produces a realized gain/loss the
instant it happens, but `architecture.md` didn't pin down where that gets recorded, and inventing a
shape for it here would be scope creep beyond what was asked for. Left as an explicit, open gap
rather than silently decided: a `realized_gains` table keyed by the triggering `transactions.id` is
the obvious future shape, and adding it later wouldn't disturb anything proposed in this document.

## `return_snapshots`

Derived, materialized, rebuildable.

| Column | Type | Notes |
|---|---|---|
| `account_id` | `BIGINT NOT NULL REFERENCES accounts(id)` | Part of PK. |
| `canonical_ticker` | `VARCHAR(20) NOT NULL` | Part of PK. Same column, same reasoning (no FK) as `position_snapshots.canonical_ticker` above. |
| `price_date` | `DATE NOT NULL` | Part of PK. Daily grain, unlike `position_snapshots` — a return changes every priced day even with zero transactions, mirroring `market_returns`'s own daily grain in data-store. |
| `quantity` | `NUMERIC(20,6) NOT NULL` | Carried from the latest `position_snapshots` row matching `(account_id, canonical_ticker)` with `as_of_date <= price_date`, denormalized so a reader needs no join back to reconstruct it. |
| `market_value_native` | `NUMERIC(20,4) NOT NULL` | `quantity` times the `data-store` price on `price_date`, in the instrument's trading currency. |
| `market_value_eur` | `NUMERIC(20,4) NOT NULL` | Converted using `data-store`'s FX rate series for `price_date` — see [`flows.md`](flows.md)'s cross-service prerequisite. |
| `cost_basis_eur` | `NUMERIC(20,4) NOT NULL` | Carried from `position_snapshots.cost_basis_eur`. |
| `unrealized_return_eur` | `NUMERIC(20,4) NOT NULL` | `market_value_eur - cost_basis_eur`. |
| `unrealized_return_pct` | `NUMERIC(18,8)` | `unrealized_return_eur / cost_basis_eur`. Nullable — undefined (not zero) when `cost_basis_eur = 0`, i.e. the row for the trading day a position sells down to zero quantity. That row is still written (the position was open that day), but no further rows follow it — see `architecture.md`'s bounded-grain note on `ReturnSnapshot`. |
| `dividends_received_eur` | `NUMERIC(20,4) NOT NULL DEFAULT 0` | **Added at task 8, amending this table's original sign-off.** Cumulative signed sum of this `(account_id, canonical_ticker)`'s `DIVIDEND`-type `transactions.eur_amount` with `trade_datetime` (date, UTC) `<= price_date`. Needs no FX arithmetic at all — `eur_amount` is already frozen in EUR at transaction time (this table's own "immutable source of truth" convention, same as `transactions`), so this is a running sum over already-stored data, never a `datastore_client` call. A signed sum also nets out withholding tax automatically **if** the broker plugin types that tax as its own negative `DIVIDEND` row; if a plugin instead types it as a `FEE`, it isn't captured here — `FEE`/`ADJUSTMENT` still feed nothing, the same open gap as the missing `realized_gains` table below. |
| `total_return_eur` | `NUMERIC(20,4) NOT NULL DEFAULT 0` | **Added at task 8.** `unrealized_return_eur + dividends_received_eur` — price change plus the income the position produced. `unrealized_return_eur` deliberately stays price-only; this is the separate "what did this holding actually return" figure, mirroring how `market_returns` keeps `price_return` and `simple_return` (price+dividend) as separate columns rather than merging them. |
| `total_return_pct` | `NUMERIC(18,8)` | **Added at task 8.** `total_return_eur / cost_basis_eur`. Nullable on exactly the same rule as `unrealized_return_pct`: undefined, not zero, when `cost_basis_eur = 0`. |
| `calculated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Stamped when the row is written. Every rebuild rewrites a pair's rows (see the loading strategy below), so it reflects the last rebuild, not the first, the same as `market_returns.calculated_at`. |

**The three dividend columns above are a schema amendment made mid-task 8, not part of the
original sign-off.** The table as originally designed computed only mark-to-market return
(`unrealized_return_eur`), with no path for `DIVIDEND` transactions at all — an oversight against
`architecture.md`'s own task-7 note that cash events are "left entirely to `returns/`." Per
[`data-schema.md`](../../../.claude/rules/data-schema.md) this addition was proposed and signed
off before being written: three new columns only (`dividends_received_eur`, `total_return_eur`,
`total_return_pct`), no other table, index, or key touched. `db/schema.sql` carries both the
updated `CREATE TABLE IF NOT EXISTS` (so a fresh database gets the columns in their natural
position) and an idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` below it (so a database
created during tasks 1–7 gets migrated on its next `portfolio db init`) — the same
pattern data-store's `etf_holdings.weight_percent` widening uses, minus the `DO $$` existence
guard a plain column addition doesn't need. The `NOT NULL DEFAULT 0` on the two non-nullable new
columns never actually backfills a real row: `return_snapshots` had no writer at all before task
8, so every pre-task-8 database's table is empty.

`PRIMARY KEY (account_id, canonical_ticker, price_date)`.

**Loading strategy: delete-then-insert per `(account_id, canonical_ticker)`, plus pruning — the
same as `position_snapshots`.** Each `rebuild-returns` run computes a pair's whole window (its
first position snapshot through today, or through its closing date), then replaces that pair's
rows with it in one transaction. A ticker whose position snapshots are all gone loses its return
rows too, in a transaction of its own. Which dates get a row is still bounded by the open-window
rule in `architecture.md`'s `ReturnSnapshot` note: `rebuild-returns` stops adding new dates for a
position once it closes to zero, even as `data-store`'s calendar keeps extending past that point.

*Amended 2026-10-10 (signed off by the user), replacing a true upsert
(`INSERT ... ON CONFLICT ... DO UPDATE`, matching `market_returns`).* The upsert's reasoning was
that the trading-day calendar comes from data-store's price series, so a rebuild only overwrites
the values at an already-written date. That holds while the transaction log only grows, and
stops holding once a transaction can be deleted (a one-off spike, not a tracked task): deleting an
earlier BUY moves a pair's window start later, and deleting a ticker's only BUY leaves it no
position snapshots, so the rebuild never visits it again. Either way an upsert leaves rows no
transaction supports. The same gap already existed for a re-pointed ticker mapping, whose old
ticker's return rows outlived its pruned position snapshots and still showed on the account and
home pages. Replacing a pair loses nothing a current transaction supports, since the whole window
is recomputed. The one exception: if data-store ever lost price history it once served, the rows
for those dates would go too rather than be kept. No DDL changed.

What stays untouched: a pair whose data-store fetch or compute fails keeps its previous rows (the
replacement only runs once the new rows exist), and so does a ticker data-store doesn't track.
A ticker is only pruned once it has no position snapshots, and `rebuild-positions` never prunes in
an account with a blocked instrument ([`flows.md`](flows.md)'s "Pruning a ticker"), so "blocked
means don't touch" carries over unchanged.

## Row-level security

Signed off 2026-11-28 (TA-6, #211). No columns changed: this section is policies and one role.

**Which tables.** `accounts`, `transactions`, `position_snapshots` and `return_snapshots` each
have `ENABLE` and `FORCE ROW LEVEL SECURITY` and one policy, `<table>_owner`. `FORCE` matters
because the `portfolio` role owns these tables, and a table's owner skips RLS without it.
`ticker_mappings` has no policy: a mapping is global, and every user's rebuild reads the same
rows.

**The rule.** Each policy compares against the session setting `app.user_id`, which
`db/connection.user_connection` sets:
- On `accounts`, the check is `user_id = NULLIF(current_setting('app.user_id', true), '')::uuid`.
- On the three child tables, it is `account_id IN (SELECT id FROM accounts WHERE user_id = …)`
  with the same expression.

The same expression is both `USING` and `WITH CHECK`. So a connection can neither read another
user's rows nor write a row into another user's account. An update or delete aimed at another
user's rows matches nothing.

**Deny by default.** With no user set, `current_setting(..., true)` is `NULL`, or `''` after a
reset, which the `NULLIF` turns into `NULL`. The comparison is then never true. A connection
without a user sees no rows, and its inserts fail with `InsufficientPrivilege`.

**Child tables check their owner through `accounts`, not a copied `user_id`.** Copying `user_id`
into three tables would need a migration, a backfill, and a composite foreign key to stop it
drifting from `accounts.user_id`. The subquery is a lookup on `idx_accounts_user_id` that
Postgres runs once per query, returning a handful of ids. Revisit only if it ever shows up in a
profile.

**Not governed by RLS:**
- `TRUNCATE` and DDL.
- Foreign-key checks, which Postgres runs without policies.
- Superusers, and the read-only `portfolio_cross_user` role (`BYPASSRLS`, `SELECT` only).
  Its grants and the reason it exists are in [`deployment.md`](deployment.md) and
  [`architecture.md`](architecture.md)'s "User isolation".

**Idempotency.** `ENABLE` and `FORCE` are no-ops on a rerun. Each policy is `DROP POLICY IF
EXISTS` then `CREATE POLICY`, so `db init` rewrites it to `schema.sql`'s current definition.

## Open items, carried forward rather than resolved here

- **Realized gains** have no table yet (see `position_snapshots` above).
- **`position_snapshots.native_currency`'s single-currency-per-instrument assumption is scoped to
  one account, and a violation is handled, not just flagged** (task 7): within one account it warns
  but still computes (`positions/calculator.py`'s anomaly reporting); across two accounts it isn't
  a violation at all (see the `native_currency`/`average_cost_eur` notes above). Still not verified
  against a real multi-currency same-ticker case in a single account — only exercised by tests.
- **The `data-store` read API** this schema's `return_snapshots` rebuild depends on now exists
  (walking-skeleton task 3, [`walking-skeleton.md`](../../../docs/roadmap-archive/walking-skeleton.md)) — see [`flows.md`](flows.md)'s cross-service prerequisite.
  **Built as of task 8**: `datastore_client/http_client.py`'s `get_price_series`,
  `get_fx_rate_series`, and `get_ticker_info`, alongside `get_fx_rate` (task 14). `accounts`,
  `transactions`, and `position_snapshots` need nothing from it either way.
- **`dividends_received_eur`'s signed-sum netting depends on how a broker plugin classifies
  withholding tax** (task 8): a tax row typed as `DIVIDEND` (even negative) nets out correctly; one
  typed as `FEE` doesn't get captured at all, since `FEE`/`ADJUSTMENT` still feed nothing. Worth
  checking against each installed plugin's own row classification, documented in that plugin's own
  repo (see [`broker-import.md`](broker-import.md)), not assumed here.
- **A `DIVIDEND` transaction's instrument can fail to resolve to the same `canonical_ticker` its
  `BUY`/`SELL` rows did** (task 8), the identical sharp edge [`flows.md`](flows.md) already documents
  for `position_snapshots`: if one account's dividend rows for a holding arrive under a different,
  unmapped identifier kind than its trade rows, that dividend is silently excluded from
  `dividends_received_eur` rather than blocking the ticker's rebuild. Fixed the same way — map
  every identifier kind a broker actually emits for an instrument in `ticker_mappings` — not in
  code.
- **No migration from `configs/ticker_mappings.yaml`.** The file's mappings aren't imported into
  `ticker_mappings`; a deployment re-enters them on the admin page, which lists exactly the
  instruments that need one. A one-off importer wasn't worth building for the handful of entries
  the only deployment had.
- **Deleting a mapping leaves the positions it produced.** The instrument goes back to being
  unmapped, and a blocked instrument's existing snapshots are left untouched ([`flows.md`](flows.md)'s
  "blocked means don't touch"), so they stay until the instrument is mapped again. Re-pointing a
  mapping prunes the old ticker's snapshots, except in an account that still holds some other
  unmapped instrument: pruning is skipped for any account with a blocked instrument.
- **`accounts.user_id` is unvalidated at write time.** There's no foreign key (no local `users`
  table to reference) and no call to `auth` to confirm the UUID is real — `account add` accepts any
  syntactically valid UUID. Acceptable for a solo hobby deployment; worth revisiting once `auth` has
  a real API to check against.
- **A real `auth`-issued UUID is now obtainable** — `auth`'s signup endpoint shipped in task 2 — but
  `account add` still doesn't call it: the UUID is pasted in by the caller either way, whether it
  came from a real signup or was hand-generated for testing. The unvalidated-at-write-time gap
  above is unchanged; only the "nothing to test with" half of this item is resolved.
