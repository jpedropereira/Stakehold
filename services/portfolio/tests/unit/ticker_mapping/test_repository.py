"""The SQL itself -- the upsert against the two partial unique indexes, the CHECK constraint --
is covered against a real Postgres in tests/integration/test_ticker_mappings.py. These cover
only how rows become the in-memory lookup."""

from portfolio.ticker_mapping.repository import load_all


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, rows):
        self._cursor = FakeCursor(rows)

    def cursor(self):
        return self._cursor


def test_load_all_builds_isin_and_per_broker_symbol_lookups():
    conn = FakeConnection(
        rows=[
            ("ISIN", "IE00B4L5Y983", None, "IWDA.AS"),
            ("SYMBOL", "IWDA", "globex", "IWDA.AS"),
            ("SYMBOL", "SGLD", "globex", "SGLD.L"),
            ("SYMBOL", "IWDA", "initech", "IWDA.L"),
        ]
    )

    mappings = load_all(conn)

    assert mappings.isin == {"IE00B4L5Y983": "IWDA.AS"}
    assert mappings.symbol == {
        "globex": {"IWDA": "IWDA.AS", "SGLD": "SGLD.L"},
        "initech": {"IWDA": "IWDA.L"},
    }


def test_load_all_with_no_rows_maps_nothing():
    mappings = load_all(FakeConnection(rows=[]))

    assert mappings.resolve("ISIN", "IE00B4L5Y983", broker="acme") is None
