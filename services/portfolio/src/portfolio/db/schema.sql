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

-- The immutable ledger. No broker column: an account has exactly one broker, so it would be
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
