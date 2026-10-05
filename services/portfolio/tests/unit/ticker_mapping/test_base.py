import pytest
from portfolio.errors import TickerMappingError
from portfolio.ticker_mapping.base import TickerMappings


def _mappings():
    return TickerMappings(
        isin={"IE00B4L5Y983": "IWDA.AS"},
        symbol={"globex": {"IWDA": "IWDA.AS"}},
    )


def test_isin_mappings_are_global_regardless_of_broker():
    mappings = _mappings()

    assert mappings.resolve("ISIN", "IE00B4L5Y983", broker="globex") == "IWDA.AS"
    assert mappings.resolve("ISIN", "IE00B4L5Y983", broker="acme") == "IWDA.AS"


def test_symbol_mapping_is_scoped_per_broker():
    mappings = _mappings()

    assert mappings.resolve("SYMBOL", "IWDA", broker="globex") == "IWDA.AS"
    assert mappings.resolve("SYMBOL", "IWDA", broker="acme") is None


def test_a_ticker_identifier_resolves_to_itself_without_any_mapping():
    mappings = TickerMappings(isin={}, symbol={})

    assert mappings.resolve("TICKER", "IWDA.AS", broker="acme") == "IWDA.AS"


def test_resolve_returns_none_for_an_unmapped_isin():
    assert _mappings().resolve("ISIN", "US0000000000", broker="acme") is None


def test_resolve_rejects_an_unrecognized_kind():
    with pytest.raises(TickerMappingError, match="CUSIP"):
        _mappings().resolve("CUSIP", "037833100", broker="globex")
