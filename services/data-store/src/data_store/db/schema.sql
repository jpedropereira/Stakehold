CREATE TABLE IF NOT EXISTS etf_holdings (
    id              BIGSERIAL PRIMARY KEY,
    fund_key        VARCHAR(50)  NOT NULL,
    as_of_date      DATE         NOT NULL,
    holding_ticker  VARCHAR(20),
    holding_name    VARCHAR(200),
    sector          VARCHAR(100),
    asset_class     VARCHAR(100),
    market_value    NUMERIC(20,2),
    weight_percent  NUMERIC(9,4),
    notional_value  NUMERIC(20,2),
    shares          NUMERIC(20,4),
    price           NUMERIC(18,6),
    location        VARCHAR(100),
    exchange        VARCHAR(100),
    currency        VARCHAR(10),
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Widens weight_percent on databases created before this change. NUMERIC(6,4) capped at
-- 99.9999, so a >=100% line (a fully concentrated position, or a leveraged/derivative leg)
-- raised a numeric overflow -- and since a snapshot loads as one COPY, that lost the whole
-- fund's snapshot rather than the single bad row. Widening only: every value that fit before
-- still fits. Guarded on the current precision so a rerun doesn't rewrite the table for nothing.
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_name = 'etf_holdings'
          AND column_name = 'weight_percent'
          AND numeric_precision = 6
    ) THEN
        ALTER TABLE etf_holdings ALTER COLUMN weight_percent TYPE NUMERIC(9,4);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_etf_holdings_fund_date ON etf_holdings (fund_key, as_of_date);
CREATE INDEX IF NOT EXISTS idx_etf_holdings_ticker    ON etf_holdings (holding_ticker);

CREATE TABLE IF NOT EXISTS raw_market_data (
    ticker       VARCHAR(20)   NOT NULL,
    price_date   DATE          NOT NULL,
    source       VARCHAR(20)   NOT NULL,
    asset_type   VARCHAR(20)   NOT NULL,
    open         NUMERIC(18,4),
    high         NUMERIC(18,4),
    low          NUMERIC(18,4),
    close        NUMERIC(18,4),
    adj_close    NUMERIC(18,4),
    volume       BIGINT,
    dividends    NUMERIC(18,4)  NOT NULL DEFAULT 0,
    stock_splits NUMERIC(18,6)  NOT NULL DEFAULT 0,
    ingested_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, price_date, source)
);

CREATE INDEX IF NOT EXISTS idx_raw_market_data_ticker_date ON raw_market_data (ticker, price_date);

CREATE TABLE IF NOT EXISTS market_returns (
    ticker                VARCHAR(20)    NOT NULL,
    price_date            DATE           NOT NULL,
    source                VARCHAR(20)    NOT NULL,
    split_adjusted_close  NUMERIC(18,4)  NOT NULL,
    future_split_factor   NUMERIC(30,15) NOT NULL,
    price_return          NUMERIC(18,8),
    dividend_return       NUMERIC(18,8),
    simple_return         NUMERIC(18,8),
    log_return            NUMERIC(18,8),
    calculated_at         TIMESTAMPTZ    NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, price_date, source)
);

-- Widens future_split_factor on databases created before this change. NUMERIC(18,6) bottomed
-- out at 1e-6, so a ticker with cumulative reverse splits underflowed to a stored 0 -- BMPS.MI's
-- four reverse splits multiply out to 5e-8, and 3,814 of its rows were stored as 0. That reads
-- as a valid factor rather than a missing one, and silently zeroes the documented
-- `split_adjusted_close * future_split_factor` as-traded derivation. NUMERIC(30,15) also matches
-- the float64 the factor is computed in, so it stores no false precision. Widening only: every
-- value that fit before still fits. Guarded on the current precision so a rerun is a no-op.
-- Rerun `data-store calculate-returns` after this to rewrite any already-zeroed rows.
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_name = 'market_returns'
          AND column_name = 'future_split_factor'
          AND numeric_precision = 18
    ) THEN
        ALTER TABLE market_returns ALTER COLUMN future_split_factor TYPE NUMERIC(30,15);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_market_returns_ticker_date ON market_returns (ticker, price_date);

CREATE TABLE IF NOT EXISTS regimes (
    regime_id   VARCHAR(50)   PRIMARY KEY,
    dimension   VARCHAR(30)   NOT NULL,
    scope       VARCHAR(20)   NOT NULL,
    name        VARCHAR(200)  NOT NULL,
    start_date  DATE          NOT NULL,
    end_date    DATE,
    known_from  DATE,
    severity    VARCHAR(20),
    source      VARCHAR(50)   NOT NULL,
    notes       TEXT,
    ingested_at TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_regimes_dimension_scope ON regimes (dimension, scope);
CREATE INDEX IF NOT EXISTS idx_regimes_start_date        ON regimes (start_date);
