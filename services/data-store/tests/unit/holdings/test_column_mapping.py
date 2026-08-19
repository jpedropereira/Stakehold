from data_store.holdings.column_mapping import COLUMN_MAPPING

EXPECTED_DB_COLUMNS = {
    "holding_ticker",
    "holding_name",
    "sector",
    "asset_class",
    "market_value",
    "weight_percent",
    "notional_value",
    "shares",
    "price",
    "location",
    "exchange",
    "currency",
}


def test_covers_every_csv_sourced_holdings_column():
    assert set(COLUMN_MAPPING.keys()) == EXPECTED_DB_COLUMNS


def test_every_entry_has_csv_col_and_dtype():
    for db_col, spec in COLUMN_MAPPING.items():
        assert "csv_col" in spec, f"{db_col} missing csv_col"
        assert spec["dtype"] in {"str", "float"}, f"{db_col} has unsupported dtype"


def test_csv_col_names_are_unique():
    csv_cols = [spec["csv_col"] for spec in COLUMN_MAPPING.values()]
    assert len(csv_cols) == len(set(csv_cols))
