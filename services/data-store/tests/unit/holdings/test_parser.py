import pandas as pd
import pytest
from data_store.holdings.parser import (
    ParseError,
    fetch_holdings_csv,
    parse_holdings_csv,
)

PREAMBLE = 'Fund Holdings as of,"13/Aug/2026"\n\n'
CSV_TABLE = (
    "Ticker,Name,Weight (%)\n" '"AAPL","APPLE INC","5.12"\n' '"MSFT","MICROSOFT CORP","4.87"\n'
)


def test_extracts_table_after_preamble():
    content = PREAMBLE + CSV_TABLE

    df = parse_holdings_csv(content)

    assert list(df.columns) == ["ticker", "name", "weight (%)"]
    assert df["ticker"].tolist() == ["AAPL", "MSFT"]


def test_lowercases_column_names():
    content = PREAMBLE + 'Ticker,Asset Class\n"AAPL","Equity"\n'

    df = parse_holdings_csv(content)

    assert "asset class" in df.columns
    assert "Asset Class" not in df.columns


def test_raises_when_header_row_not_found():
    with pytest.raises(ParseError, match="header row"):
        parse_holdings_csv("Some unexpected content\nwithout a holdings table\n")


def test_fetch_holdings_csv_parses_response_text(monkeypatch):
    content = PREAMBLE + CSV_TABLE

    class FakeResponse:
        text = content
        encoding = None

        def raise_for_status(self):
            pass

    captured_url = {}

    def fake_get(url):
        captured_url["url"] = url
        return FakeResponse()

    monkeypatch.setattr("data_store.holdings.parser.requests.get", fake_get)

    df = fetch_holdings_csv("https://example.com/holdings.csv")

    assert captured_url["url"] == "https://example.com/holdings.csv"
    assert isinstance(df, pd.DataFrame)
    assert df["ticker"].tolist() == ["AAPL", "MSFT"]


def test_fetch_holdings_csv_propagates_http_errors(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            raise RuntimeError("404 Not Found")

    monkeypatch.setattr("data_store.holdings.parser.requests.get", lambda url: FakeResponse())

    with pytest.raises(RuntimeError, match="404"):
        fetch_holdings_csv("https://example.com/holdings.csv")
