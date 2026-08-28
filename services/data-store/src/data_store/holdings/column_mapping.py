# Maps etf_holdings DB columns to the (lowercased) column name iShares uses in its holdings
# CSV export, plus the dtype to cast to. Columns not sourced from the CSV (id, fund_key,
# as_of_date, ingested_at) are not listed here — those are supplied by the caller.
COLUMN_MAPPING = {
    "holding_ticker": {"csv_col": "ticker", "dtype": "str"},
    "holding_name": {"csv_col": "name", "dtype": "str"},
    "sector": {"csv_col": "sector", "dtype": "str"},
    "asset_class": {"csv_col": "asset class", "dtype": "str"},
    "market_value": {"csv_col": "market value", "dtype": "float"},
    "weight_percent": {"csv_col": "weight (%)", "dtype": "float"},
    "notional_value": {"csv_col": "notional value", "dtype": "float"},
    "shares": {"csv_col": "shares", "dtype": "float"},
    "price": {"csv_col": "price", "dtype": "float"},
    "location": {"csv_col": "location", "dtype": "str"},
    "exchange": {"csv_col": "exchange", "dtype": "str"},
    "currency": {"csv_col": "market currency", "dtype": "str"},
}
