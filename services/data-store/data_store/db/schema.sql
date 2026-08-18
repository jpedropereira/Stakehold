CREATE TABLE IF NOT EXISTS etf_holdings (
    id              BIGSERIAL PRIMARY KEY,
    fund_key        VARCHAR(50)  NOT NULL,
    as_of_date      DATE         NOT NULL,
    holding_ticker  VARCHAR(20),
    holding_name    VARCHAR(200),
    sector          VARCHAR(100),
    asset_class     VARCHAR(100),
    market_value    NUMERIC(20,2),
    weight_percent  NUMERIC(6,4),
    notional_value  NUMERIC(20,2),
    shares          NUMERIC(20,4),
    price           NUMERIC(18,6),
    location        VARCHAR(100),
    exchange        VARCHAR(100),
    currency        VARCHAR(10),
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_etf_holdings_fund_date ON etf_holdings (fund_key, as_of_date);
CREATE INDEX IF NOT EXISTS idx_etf_holdings_ticker    ON etf_holdings (holding_ticker);

CREATE TABLE IF NOT EXISTS daily_ohlcv (
    ticker      VARCHAR(20)  NOT NULL,
    price_date  DATE         NOT NULL,
    source      VARCHAR(20)  NOT NULL,
    asset_type  VARCHAR(20)  NOT NULL,
    open        NUMERIC(18,4),
    high        NUMERIC(18,4),
    low         NUMERIC(18,4),
    close       NUMERIC(18,4),
    adj_close   NUMERIC(18,4),
    volume      BIGINT,
    ingested_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, price_date, source)
);

-- Upgrades tables created before asset_type existed. DEFAULT is a one-time backfill
-- convenience for pre-existing rows (all equities, from ETF holdings, at the time this was
-- added) — dropped immediately after so future inserts must specify it explicitly.
ALTER TABLE daily_ohlcv ADD COLUMN IF NOT EXISTS asset_type VARCHAR(20) NOT NULL DEFAULT 'equity';
ALTER TABLE daily_ohlcv ALTER COLUMN asset_type DROP DEFAULT;

CREATE INDEX IF NOT EXISTS idx_daily_ohlcv_ticker_date ON daily_ohlcv (ticker, price_date);

-- Renamed from `dividends` -- rename-in-place (not drop+recreate) so existing data survives.
-- A no-op on a fresh DB (no `dividends` table yet) or a DB that already has `dividend_events`.
ALTER TABLE IF EXISTS dividends RENAME TO dividend_events;
ALTER INDEX IF EXISTS idx_dividends_ticker_date RENAME TO idx_dividend_events_ticker_date;

CREATE TABLE IF NOT EXISTS dividend_events (
    ticker      VARCHAR(20)   NOT NULL,
    ex_date     DATE          NOT NULL,
    source      VARCHAR(20)   NOT NULL,
    asset_type  VARCHAR(20)   NOT NULL,
    amount      NUMERIC(18,4) NOT NULL,
    ingested_at TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, ex_date, source)
);

CREATE INDEX IF NOT EXISTS idx_dividend_events_ticker_date ON dividend_events (ticker, ex_date);
