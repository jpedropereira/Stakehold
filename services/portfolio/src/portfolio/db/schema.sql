-- Portfolio's tables on app-postgres. See docs/schema.md for the signed-off column-level
-- reasoning and docs/architecture.md for why the service is laid out this way.
--
-- Every statement is guarded (IF NOT EXISTS, or a DO $$ block checking the current state)
-- so `portfolio db init` is safe to rerun against any database, new or existing.
--
-- Unqualified table names on purpose: `jobs.py`'s `run_db_init` connects here as the
-- `portfolio` role, whose default search_path resolves the unqualified name to the
-- like-named `portfolio` schema it owns (Postgres's built-in "$user" search_path entry) --
-- not `public`. `jobs.py` creates that role and schema before ever running this file.

-- One row per brokerage account a user has told portfolio about. user_id is the UUID auth
-- issues at signup -- a trusted plain column, not a foreign key: there is no local users
-- table, and portfolio never opens a connection to auth's database.
CREATE TABLE IF NOT EXISTS accounts (
    id             BIGSERIAL    PRIMARY KEY,
    user_id        UUID         NOT NULL,
    broker         VARCHAR(50)  NOT NULL,
    account_label  VARCHAR(100) NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON accounts (user_id);
-- Scoped to the owner, not global: the label is the user's own handle for the account.
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_user_label ON accounts (user_id, account_label);

-- The ledger: rows are never edited, only added or deleted by their owner. No broker column: an account has exactly one broker, so it would be
-- functionally dependent on account_id and free to disagree with it -- see docs/schema.md.
CREATE TABLE IF NOT EXISTS transactions (
    id                          BIGSERIAL     PRIMARY KEY,
    account_id                  BIGINT        NOT NULL REFERENCES accounts (id),
    broker_transaction_ref      VARCHAR(128)  NOT NULL,
    transaction_type            VARCHAR(20)   NOT NULL,
    trade_datetime              TIMESTAMPTZ   NOT NULL,
    instrument_identifier       VARCHAR(50),
    instrument_identifier_kind  VARCHAR(10),
    quantity                    NUMERIC(20,6),
    price                       NUMERIC(18,6),
    native_currency             VARCHAR(10)   NOT NULL,
    native_amount               NUMERIC(20,4) NOT NULL,
    eur_amount                  NUMERIC(20,4) NOT NULL,
    fx_rate                     NUMERIC(18,8),
    fx_rate_source              VARCHAR(20),
    fees_native                 NUMERIC(18,4),
    imported_at                 TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- The idempotent-import key. Account-scoped because a content-hash-derived ref (a broker export
-- with no natural id of its own) is one two accounts could plausibly collide on, unlike a
-- genuinely broker-global natural key some export formats provide.
CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_account_ref
    ON transactions (account_id, broker_transaction_ref);
CREATE INDEX IF NOT EXISTS idx_transactions_account_datetime
    ON transactions (account_id, trade_datetime);
-- Powers list-unmapped-instruments: the distinct identifiers to diff against the YAML
-- mapping. Partial, because cash-only rows carry no instrument and would only bloat it.
CREATE INDEX IF NOT EXISTS idx_transactions_instrument
    ON transactions (instrument_identifier_kind, instrument_identifier)
    WHERE instrument_identifier IS NOT NULL;

-- Derived, materialized, rebuildable. Event-driven grain: one row per date a transaction
-- actually changed the position; "as of date D" reads as the latest row with as_of_date <= D.
CREATE TABLE IF NOT EXISTS position_snapshots (
    account_id           BIGINT        NOT NULL REFERENCES accounts (id),
    canonical_ticker     VARCHAR(20)   NOT NULL,
    as_of_date           DATE          NOT NULL,
    quantity             NUMERIC(20,6) NOT NULL,
    average_cost_native  NUMERIC(18,6) NOT NULL,
    native_currency      VARCHAR(10)   NOT NULL,
    average_cost_eur     NUMERIC(18,6) NOT NULL,
    cost_basis_eur       NUMERIC(20,4) NOT NULL,
    calculated_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, canonical_ticker, as_of_date)
);

-- Derived, materialized, rebuildable. Daily grain, unlike position_snapshots: a return moves
-- every priced day even with no transactions, mirroring data-store's market_returns.
CREATE TABLE IF NOT EXISTS return_snapshots (
    account_id              BIGINT        NOT NULL REFERENCES accounts (id),
    canonical_ticker        VARCHAR(20)   NOT NULL,
    price_date              DATE          NOT NULL,
    quantity                NUMERIC(20,6) NOT NULL,
    market_value_native     NUMERIC(20,4) NOT NULL,
    market_value_eur        NUMERIC(20,4) NOT NULL,
    cost_basis_eur          NUMERIC(20,4) NOT NULL,
    -- Price-only -- market_value_eur minus cost_basis_eur. Dividends deliberately do not enter
    -- this column; see dividends_received_eur/total_return_eur below (docs/schema.md).
    unrealized_return_eur   NUMERIC(20,4) NOT NULL,
    -- Nullable on purpose: undefined, not zero, when a position closes to a zero cost basis.
    unrealized_return_pct   NUMERIC(18,8),
    -- Signed cumulative sum of this (account, ticker)'s DIVIDEND-type transactions.eur_amount
    -- up to and including price_date -- already EUR, frozen at transaction time, so this needs
    -- no FX arithmetic. See docs/schema.md.
    dividends_received_eur  NUMERIC(20,4) NOT NULL DEFAULT 0,
    -- unrealized_return_eur + dividends_received_eur: price change plus the income the
    -- position produced.
    total_return_eur        NUMERIC(20,4) NOT NULL DEFAULT 0,
    -- Nullable on the same rule as unrealized_return_pct.
    total_return_pct        NUMERIC(18,8),
    calculated_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, canonical_ticker, price_date)
);

-- Migrates a database created before dividends were added to return_snapshots (tasks 1-7).
-- ADD COLUMN IF NOT EXISTS is already idempotent -- no DO $$ guard needed, unlike a type change.
ALTER TABLE return_snapshots
    ADD COLUMN IF NOT EXISTS dividends_received_eur NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_return_eur       NUMERIC(20,4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_return_pct       NUMERIC(18,8);

-- Global, not per user: one instrument is the same instrument for everyone. Written only by an
-- admin (gated in web, see docs/architecture.md). Replaces configs/ticker_mappings.yaml.
CREATE TABLE IF NOT EXISTS ticker_mappings (
    id                BIGSERIAL    PRIMARY KEY,
    identifier_kind   VARCHAR(10)  NOT NULL,
    identifier        VARCHAR(50)  NOT NULL,
    broker            VARCHAR(50),
    canonical_ticker  VARCHAR(20)  NOT NULL,
    -- The auth user id of the admin who last set it -- a trusted plain column, like user_id.
    updated_by        UUID         NOT NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- An ISIN identifies one instrument worldwide, so its mapping covers every broker; a bare
    -- symbol doesn't, so its mapping is scoped to one broker. TICKER never needs a mapping.
    CONSTRAINT ticker_mappings_kind_broker CHECK (
        (identifier_kind = 'ISIN' AND broker IS NULL)
        OR (identifier_kind = 'SYMBOL' AND broker IS NOT NULL)
    )
);

-- Two partial indexes rather than one over (identifier_kind, identifier, broker): a NULL
-- broker never equals another NULL, so a single index would let an ISIN be mapped twice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticker_mappings_isin
    ON ticker_mappings (identifier) WHERE identifier_kind = 'ISIN';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticker_mappings_symbol
    ON ticker_mappings (broker, identifier) WHERE identifier_kind = 'SYMBOL';

-- A user's saved, named Labs mixes (LF-1). Per user, not per account: a mix is any set of
-- tickers, not a holding. user_id is a trusted plain column, like accounts.user_id.
CREATE TABLE IF NOT EXISTS saved_labs (
    id          BIGSERIAL     PRIMARY KEY,
    user_id     UUID          NOT NULL,
    name        VARCHAR(100)  NOT NULL,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Scoped to the owner, like idx_accounts_user_label: the name is the user's handle for the lab.
-- Also serves "list my labs", since user_id leads it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_labs_user_name ON saved_labs (user_id, name);

-- One row per asset in a lab. Unlike every other foreign key here, this one cascades: an asset
-- row means nothing without its lab, and nothing else references it -- see docs/schema.md.
CREATE TABLE IF NOT EXISTS saved_lab_assets (
    saved_lab_id  BIGINT         NOT NULL REFERENCES saved_labs (id) ON DELETE CASCADE,
    -- The row's place in the form, 0-based, so a lab opens in the order it was saved.
    position      SMALLINT       NOT NULL,
    ticker        VARCHAR(20)    NOT NULL,
    -- The percentage as typed, unscaled: the backtest scales a mix's weights to 100%.
    weight_pct    NUMERIC(12,6)  NOT NULL CHECK (weight_pct > 0),
    PRIMARY KEY (saved_lab_id, position),
    UNIQUE (saved_lab_id, ticker)
);

-- Row-level security (TA-6). A second fence under the repositories' own user scoping: a query
-- that forgets its user filter still sees only the connection's own user's rows. The user comes
-- from the session setting app.user_id, set by db/connection.user_connection. With no user set,
-- current_setting(..., true) is NULL (or '' once set and reset), the comparison is never true,
-- so a connection without a user reads no rows and writes none -- deny by default.
--
-- FORCE because the portfolio role owns these tables, and a table's owner skips RLS otherwise.
-- Child tables check their owner through accounts rather than carrying a copied user_id, which
-- could drift from accounts.user_id. ticker_mappings is global, so it has no policy. Cross-user
-- reads go through a separate read-only BYPASSRLS role that jobs.run_db_init creates (its name
-- comes from configuration, so it isn't in this file). See docs/schema.md.
--
-- DROP + CREATE rather than a guard: a rerun rewrites each policy to this file's definition,
-- so editing one here takes effect on the next `db init`. ENABLE/FORCE are already idempotent.
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS accounts_owner ON accounts;
CREATE POLICY accounts_owner ON accounts
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS transactions_owner ON transactions;
CREATE POLICY transactions_owner ON transactions
    USING (account_id IN (
        SELECT id FROM accounts
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid))
    WITH CHECK (account_id IN (
        SELECT id FROM accounts
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid));

ALTER TABLE position_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE position_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS position_snapshots_owner ON position_snapshots;
CREATE POLICY position_snapshots_owner ON position_snapshots
    USING (account_id IN (
        SELECT id FROM accounts
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid))
    WITH CHECK (account_id IN (
        SELECT id FROM accounts
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid));

ALTER TABLE return_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE return_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS return_snapshots_owner ON return_snapshots;
CREATE POLICY return_snapshots_owner ON return_snapshots
    USING (account_id IN (
        SELECT id FROM accounts
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid))
    WITH CHECK (account_id IN (
        SELECT id FROM accounts
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid));

-- Saved labs (LF-1) are per user, not per account, so saved_labs carries its owner directly and
-- saved_lab_assets checks it through saved_labs, as the account-owned tables do through accounts.
-- No cross-user command reads them, so db/roles.py doesn't grant them to the cross-user role.
ALTER TABLE saved_labs ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_labs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS saved_labs_owner ON saved_labs;
CREATE POLICY saved_labs_owner ON saved_labs
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

ALTER TABLE saved_lab_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_lab_assets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS saved_lab_assets_owner ON saved_lab_assets;
CREATE POLICY saved_lab_assets_owner ON saved_lab_assets
    USING (saved_lab_id IN (
        SELECT id FROM saved_labs
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid))
    WITH CHECK (saved_lab_id IN (
        SELECT id FROM saved_labs
        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid));
