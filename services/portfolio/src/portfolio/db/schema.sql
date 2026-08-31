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

-- The idempotent-import key. Account-scoped because IBKR's ref is a content hash two
-- accounts could plausibly collide on, unlike Degiro's globally unique Order ID.
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
    account_id             BIGINT        NOT NULL REFERENCES accounts (id),
    canonical_ticker       VARCHAR(20)   NOT NULL,
    price_date             DATE          NOT NULL,
    quantity               NUMERIC(20,6) NOT NULL,
    market_value_native    NUMERIC(20,4) NOT NULL,
    market_value_eur       NUMERIC(20,4) NOT NULL,
    cost_basis_eur         NUMERIC(20,4) NOT NULL,
    unrealized_return_eur  NUMERIC(20,4) NOT NULL,
    -- Nullable on purpose: undefined, not zero, when a position closes to a zero cost basis.
    unrealized_return_pct  NUMERIC(18,8),
    calculated_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, canonical_ticker, price_date)
);
