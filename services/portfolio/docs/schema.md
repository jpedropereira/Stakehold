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

Four tables: `accounts` (who owns what — `user_id` is a plain UUID referencing `auth`, not a local
`users` table), `transactions` (the immutable ledger), and `position_snapshots` /
`return_snapshots` (derived, materialized, rebuildable). The manual broker-instrument →
`data-store`-ticker mapping is **not** a table here — it's defined in a YAML config file for now;
see "Ticker mappings — not a table here" below.

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

**Loading strategy**: single-row insert via `account add --user <id> --broker <key>`, where `<id>`
is the UUID `auth` issued at signup — pasted in by the caller, not looked up or validated against
`auth` (see open items).

## Ticker mappings — not a table here

The manual mapping from a broker-reported instrument to the canonical ticker `data-store` prices
was originally designed as a `ticker_links` table (two partial unique indexes enforcing ISIN-global
/ symbol-per-broker uniqueness, a `link-ticker` CLI upsert) and signed off on as such in an earlier
pass of this document. That table is **deliberately removed here, not an oversight** — mappings are
now defined in `configs/ticker_mappings.yaml` instead. See `architecture.md`'s "Configuration"
section and its cross-cutting decision on ticker-mapping storage for the reasoning, the sharp edge
(the file is deployment-wide, not per-user), and the not-yet-designed migration path back to a
database table once a UI exists.

The practical consequence for this document: `position_snapshots` and `return_snapshots` below
reference a `canonical_ticker` string directly rather than a `ticker_link_id` foreign key, since
there's no database row left to point at.

## `transactions`

The immutable ledger. One row per broker-reported line item or manually entered transaction.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `account_id` | `BIGINT NOT NULL REFERENCES accounts(id)` | |
| `broker_transaction_ref` | `VARCHAR(128) NOT NULL` | Acme's `Order ID` (a real, broker-issued natural key) or, for Globex, a content hash of (date, symbol, type, amounts) — see `architecture.md`'s disclosed Globex-row-identity risk. The exact hash input fields still need confirming before `broker_import/globex.py` is written; not decided here. |
| `transaction_type` | `VARCHAR(20) NOT NULL` | `BUY` \| `SELL` \| `DEPOSIT` \| `WITHDRAWAL` \| `DIVIDEND` \| `FX` \| `FEE` \| `ADJUSTMENT`. App-layer validated, not a DB `CHECK` — an unrecognized broker transaction type is meant to be a parser-level error (per `architecture.md`), not something that reaches this table at all. |
| `trade_datetime` | `TIMESTAMPTZ NOT NULL` | |
| `instrument_identifier` | `VARCHAR(50)` | `NULL` for cash-only rows (deposits, adjustments). |
| `instrument_identifier_kind` | `VARCHAR(10)` | `'ISIN'` \| `'SYMBOL'`, `NULL` alongside `instrument_identifier`. |
| `quantity` | `NUMERIC(20,6)` | Signed: `+` buy, `-` sell. `NULL` for cash-only rows. Six fractional digits to accommodate fractional-share brokers. |
| `price` | `NUMERIC(18,6)` | Per unit, native currency. `NULL` for cash-only rows. |
| `native_currency` | `VARCHAR(10) NOT NULL` | ISO 4217, exactly as the broker reports it — same column width as `etf_holdings.currency`. |
| `native_amount` | `NUMERIC(20,4) NOT NULL` | Signed cash-flow, the broker's own figure — the immutable source of truth (see `architecture.md`'s currency decision). |
| `eur_amount` | `NUMERIC(20,4) NOT NULL` | Signed, frozen at transaction time — never recomputed later even if an FX source is subsequently revised. |
| `fx_rate` | `NUMERIC(18,8)` | Nullable — absent when `native_currency = 'EUR'`. |
| `fx_rate_source` | `VARCHAR(20)` | `'broker_reported'` \| `'companion_row'` \| `'datastore_series'`, nullable alongside `fx_rate`. |
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
  instrument_identifier IS NOT NULL` — this is what powers `list-unmapped-instruments`'s read: find
  distinct `(kind, identifier)` pairs here, then diff them in application code against the keys
  loaded from `configs/ticker_mappings.yaml`. There's no DB anti-join for this anymore, since the
  mapping isn't a database table.

**No foreign key to a ticker-mapping table** — there isn't one. A transaction must be importable
*before* its instrument is mapped — that's a first-class, visible state, not an error — so
`instrument_identifier`/`kind` are stored exactly as reported and resolved against the loaded YAML
mapping at rebuild time, not resolved once and cached on the row. Caching the resolved
`canonical_ticker` here would go stale silently the moment the YAML file is edited.

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
| `canonical_ticker` | `VARCHAR(20) NOT NULL` | Part of PK. Same spelling/width as `data-store`'s `raw_market_data.ticker` — deliberately **no FK**, the same way `raw_market_data.ticker`/`market_returns.ticker` have no FK to any ticker-reference table in `data-store` either. Resolved from `transactions.instrument_identifier`/`kind` via `configs/ticker_mappings.yaml` at rebuild time. A snapshot can only exist once an instrument is mapped — an unmapped instrument's position is exactly what `rebuild-positions` reports as blocked, not a row with an empty ticker here. |
| `as_of_date` | `DATE NOT NULL` | Part of PK. Event-driven grain — one row per date a transaction actually changed this position. "Position as of date D" is read as the latest row with `as_of_date <= D`, not a row that must exist for every calendar date. |
| `quantity` | `NUMERIC(20,6) NOT NULL` | Running total after this date's transactions. |
| `average_cost_native` | `NUMERIC(18,6) NOT NULL` | Blended average cost per unit, in the instrument's trading currency. |
| `native_currency` | `VARCHAR(10) NOT NULL` | Assumes one trading currency per instrument per account. Flagged, not verified against a real case of the same ticker traded in two currencies on the same account. |
| `average_cost_eur` | `NUMERIC(18,6) NOT NULL` | Blended average cost per unit, in EUR. |
| `cost_basis_eur` | `NUMERIC(20,4) NOT NULL` | `quantity * average_cost_eur`, stored rather than recomputed by every reader — denormalized the same way `market_returns.split_adjusted_close` is denormalized from `raw_market_data`. |
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
scope is still per account (per `architecture.md`'s "Both rebuilds are full rebuilds" note), so
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
| `market_value_eur` | `NUMERIC(20,4) NOT NULL` | Converted using `data-store`'s FX rate series for `price_date` — see `architecture.md`'s cross-service prerequisite. |
| `cost_basis_eur` | `NUMERIC(20,4) NOT NULL` | Carried from `position_snapshots.cost_basis_eur`. |
| `unrealized_return_eur` | `NUMERIC(20,4) NOT NULL` | `market_value_eur - cost_basis_eur`. |
| `unrealized_return_pct` | `NUMERIC(18,8)` | `unrealized_return_eur / cost_basis_eur`. Nullable — undefined (not zero) when `cost_basis_eur = 0`, i.e. the row for the trading day a position sells down to zero quantity. That row is still written (the position was open that day), but no further rows follow it — see `architecture.md`'s bounded-grain note on `ReturnSnapshot`. |
| `calculated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Bumped on every upsert, same convention as `market_returns.calculated_at` — reflects the last rebuild, not the first. |

`PRIMARY KEY (account_id, canonical_ticker, price_date)`.

**Loading strategy: true upsert (`INSERT ... ON CONFLICT ... DO UPDATE`), matching
`market_returns` exactly** — unlike `position_snapshots`, the trading-day calendar itself comes from
`data-store`'s external price series, not something `rebuild-returns` invents, so the *values* at an
already-written date are only ever overwritten, never shifted to a different date. This is a stable
grid, not a fixed one, though: which dates get a row for a given `(account_id, canonical_ticker)` is
still bounded by the open-window rule in `architecture.md`'s `ReturnSnapshot` note — `rebuild-returns`
stops adding new dates for a position once it closes to zero, even as `data-store`'s calendar keeps
extending past that point.

## Open items, carried forward rather than resolved here

- **Realized gains** have no table yet (see `position_snapshots` above).
- **The Globex `broker_transaction_ref` hash's exact input fields** aren't pinned down — needs
  confirming when `broker_import/globex.py` is actually written.
- **`position_snapshots.native_currency`/`average_cost_native`'s single-currency-per-instrument
  assumption** is stated, not verified against a real multi-currency same-ticker case.
- **The `data-store` read API** this schema's `return_snapshots` rebuild depends on now exists
  (task 3 of [`tasks.md`](../../../tasks.md)) — see `architecture.md`'s cross-service prerequisite.
  What's still outstanding is portfolio's `datastore_client/http_client.py` against it, which is
  the returns task's own work. `accounts`, `transactions`, and `position_snapshots` need nothing
  from it either way.
- **The YAML → database migration path for ticker mappings**, once a UI exists, isn't designed
  yet — see `architecture.md`'s cross-cutting decision on ticker-mapping storage.
- **`accounts.user_id` is unvalidated at write time.** There's no foreign key (no local `users`
  table to reference) and no call to `auth` to confirm the UUID is real — `account add` accepts any
  syntactically valid UUID. Acceptable for a solo hobby deployment; worth revisiting once `auth` has
  a real API to check against.
- **No real UUID exists to test with yet.** `auth` doesn't have a working signup endpoint (see
  `auth/docs/architecture.md`'s open questions), so exercising `account add` today needs an ad hoc
  UUID (hand-generated, or a test fixture) rather than one actually issued by `auth`.
