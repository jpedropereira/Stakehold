import pytest
from portfolio.errors import TickerMappingError
from portfolio.ticker_mapping.loader import load_ticker_mappings


def _write(tmp_path, content):
    path = tmp_path / "ticker_mappings.yaml"
    path.write_text(content)
    return path


def test_load_ticker_mappings_parses_isin_and_symbol_sections(tmp_path):
    path = _write(
        tmp_path,
        """
        isin:
          IE00B4L5Y983: IWDA.AS
        symbol:
          globex:
            IWDA: IWDA.AS
        """,
    )

    mappings = load_ticker_mappings(path)

    assert mappings.resolve("ISIN", "IE00B4L5Y983", broker="acme") == "IWDA.AS"
    assert mappings.resolve("SYMBOL", "IWDA", broker="globex") == "IWDA.AS"


def test_isin_mappings_are_global_regardless_of_broker(tmp_path):
    path = _write(tmp_path, "isin:\n  IE00B4L5Y983: IWDA.AS\n")

    mappings = load_ticker_mappings(path)

    assert mappings.resolve("ISIN", "IE00B4L5Y983", broker="globex") == "IWDA.AS"
    assert mappings.resolve("ISIN", "IE00B4L5Y983", broker="acme") == "IWDA.AS"


def test_symbol_mapping_is_scoped_per_broker(tmp_path):
    path = _write(
        tmp_path,
        """
        symbol:
          globex:
            IWDA: IWDA.AS
        """,
    )

    mappings = load_ticker_mappings(path)

    assert mappings.resolve("SYMBOL", "IWDA", broker="globex") == "IWDA.AS"
    assert mappings.resolve("SYMBOL", "IWDA", broker="acme") is None


def test_a_ticker_identifier_resolves_to_itself_without_any_mapping(tmp_path):
    path = _write(tmp_path, "isin: {}\n")

    mappings = load_ticker_mappings(path)

    assert mappings.resolve("TICKER", "IWDA.AS", broker="acme") == "IWDA.AS"


def test_resolve_returns_none_for_an_unmapped_isin(tmp_path):
    path = _write(tmp_path, "isin:\n  IE00B4L5Y983: IWDA.AS\n")

    mappings = load_ticker_mappings(path)

    assert mappings.resolve("ISIN", "US0000000000", broker="acme") is None


def test_missing_file_raises(tmp_path):
    with pytest.raises(TickerMappingError, match="not found"):
        load_ticker_mappings(tmp_path / "nonexistent.yaml")


def test_empty_file_yields_empty_mappings(tmp_path):
    path = _write(tmp_path, "")

    mappings = load_ticker_mappings(path)

    assert mappings.resolve("ISIN", "IE00B4L5Y983", broker="acme") is None


def test_unknown_top_level_section_raises(tmp_path):
    path = _write(tmp_path, "cusip:\n  037833100: AAPL\n")

    with pytest.raises(TickerMappingError, match="cusip"):
        load_ticker_mappings(path)


def test_non_string_isin_value_raises(tmp_path):
    path = _write(tmp_path, "isin:\n  IE00B4L5Y983: 123\n")

    with pytest.raises(TickerMappingError):
        load_ticker_mappings(path)


def test_empty_isin_value_raises(tmp_path):
    path = _write(tmp_path, "isin:\n  IE00B4L5Y983: ''\n")

    with pytest.raises(TickerMappingError):
        load_ticker_mappings(path)


def test_symbol_section_must_be_nested_under_a_broker(tmp_path):
    path = _write(tmp_path, "symbol:\n  IWDA: IWDA.AS\n")

    with pytest.raises(TickerMappingError):
        load_ticker_mappings(path)


def test_resolve_rejects_an_unrecognized_kind(tmp_path):
    path = _write(tmp_path, "isin:\n  IE00B4L5Y983: IWDA.AS\n")
    mappings = load_ticker_mappings(path)

    with pytest.raises(TickerMappingError, match="CUSIP"):
        mappings.resolve("CUSIP", "037833100", broker="globex")
