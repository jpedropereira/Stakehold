from pathlib import Path

import pytest
from data_store.config_loader import (
    ConfigError,
    FundConfig,
    TickerConfig,
    load_currencies,
    load_funds,
    load_ticker_resolution,
    load_tracked_tickers,
)


def write_yaml(tmp_path, content):
    path = tmp_path / "etfs.yaml"
    path.write_text(content)
    return path


def write_currencies_yaml(tmp_path, content):
    path = tmp_path / "currencies.yaml"
    path.write_text(content)
    return path


def write_tracked_tickers_yaml(tmp_path, content):
    path = tmp_path / "tracked_tickers.yaml"
    path.write_text(content)
    return path


def write_exchange_suffixes_yaml(tmp_path, content):
    path = tmp_path / "yfinance_exchange_suffixes.yaml"
    path.write_text(content)
    return path


def test_loads_single_fund(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        funds:
          - key: ishares_msci_world
            display_name: "iShares MSCI World UCITS ETF (Acc)"
            provider: ishares
            url: "https://example.com/fund"
            isin: null
            price_source: yfinance
            enabled: true
        """,
    )

    funds = load_funds(path)

    assert funds == [
        FundConfig(
            key="ishares_msci_world",
            display_name="iShares MSCI World UCITS ETF (Acc)",
            provider="ishares",
            url="https://example.com/fund",
            isin=None,
            price_source="yfinance",
            enabled=True,
        )
    ]


def test_loads_multiple_funds(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        funds:
          - key: fund_a
            display_name: "Fund A"
            provider: ishares
            url: "https://example.com/a"
            isin: "IE0000000001"
            price_source: yfinance
            enabled: true
          - key: fund_b
            display_name: "Fund B"
            provider: ishares
            url: "https://example.com/b"
            isin: null
            price_source: yfinance
            enabled: false
        """,
    )

    funds = load_funds(path)

    assert [f.key for f in funds] == ["fund_a", "fund_b"]
    assert funds[0].isin == "IE0000000001"
    assert funds[1].enabled is False


def test_rejects_duplicate_keys(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        funds:
          - key: dup
            display_name: "A"
            provider: ishares
            url: "https://example.com/a"
            isin: null
            price_source: yfinance
            enabled: true
          - key: dup
            display_name: "B"
            provider: ishares
            url: "https://example.com/b"
            isin: null
            price_source: yfinance
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="(?i)duplicate"):
        load_funds(path)


@pytest.mark.parametrize(
    "missing_field",
    ["key", "display_name", "provider", "url", "price_source", "enabled"],
)
def test_rejects_missing_required_field(tmp_path, missing_field):
    fields = {
        "key": "fund_a",
        "display_name": "Fund A",
        "provider": "ishares",
        "url": "https://example.com/a",
        "isin": None,
        "price_source": "yfinance",
        "enabled": True,
    }
    del fields[missing_field]
    entry_lines = "\n".join(f"            {k}: {_yaml_value(v)}" for k, v in fields.items())
    path = write_yaml(tmp_path, f"funds:\n          - {entry_lines.strip()}\n")

    with pytest.raises(ConfigError, match=missing_field):
        load_funds(path)


def _yaml_value(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return f'"{value}"'


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_funds(tmp_path / "does-not-exist.yaml")


def test_rejects_empty_funds_list(tmp_path):
    path = write_yaml(tmp_path, "funds: []\n")

    with pytest.raises(ConfigError, match="no funds"):
        load_funds(path)


def test_loads_single_tracked_ticker(tmp_path):
    path = write_tracked_tickers_yaml(
        tmp_path,
        """
        tickers:
          - ticker: IWDA.AS
            display_name: "iShares Core MSCI World UCITS ETF (Amsterdam)"
            asset_type: etf
            price_source: yfinance
            enabled: true
        """,
    )

    tickers = load_tracked_tickers(path)

    assert tickers == [
        TickerConfig(
            ticker="IWDA.AS",
            display_name="iShares Core MSCI World UCITS ETF (Amsterdam)",
            asset_type="etf",
            price_source="yfinance",
            enabled=True,
        )
    ]


def test_loads_multiple_tracked_tickers_with_varied_asset_types(tmp_path):
    path = write_tracked_tickers_yaml(
        tmp_path,
        """
        tickers:
          - ticker: BABA
            display_name: "Alibaba"
            asset_type: equity
            price_source: yfinance
            enabled: true
          - ticker: 8PSG.DE
            display_name: "iShares Physical Gold ETC"
            asset_type: etc
            price_source: yfinance
            enabled: true
          - ticker: BTC-USD
            display_name: "Bitcoin"
            asset_type: crypto
            price_source: yfinance
            enabled: false
        """,
    )

    tickers = load_tracked_tickers(path)

    assert [t.ticker for t in tickers] == ["BABA", "8PSG.DE", "BTC-USD"]
    assert [t.asset_type for t in tickers] == ["equity", "etc", "crypto"]
    assert tickers[2].enabled is False


def test_rejects_duplicate_tickers(tmp_path):
    path = write_tracked_tickers_yaml(
        tmp_path,
        """
        tickers:
          - ticker: BABA
            display_name: "Alibaba"
            asset_type: equity
            price_source: yfinance
            enabled: true
          - ticker: BABA
            display_name: "Alibaba (dup)"
            asset_type: equity
            price_source: yfinance
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="(?i)duplicate"):
        load_tracked_tickers(path)


def test_rejects_invalid_asset_type(tmp_path):
    path = write_tracked_tickers_yaml(
        tmp_path,
        """
        tickers:
          - ticker: BABA
            display_name: "Alibaba"
            asset_type: bond
            price_source: yfinance
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="bond"):
        load_tracked_tickers(path)


@pytest.mark.parametrize(
    "missing_field",
    ["ticker", "display_name", "asset_type", "price_source", "enabled"],
)
def test_rejects_missing_required_ticker_field(tmp_path, missing_field):
    fields = {
        "ticker": "BABA",
        "display_name": "Alibaba",
        "asset_type": "equity",
        "price_source": "yfinance",
        "enabled": True,
    }
    del fields[missing_field]
    entry_lines = "\n".join(f"            {k}: {_yaml_value(v)}" for k, v in fields.items())
    path = write_tracked_tickers_yaml(tmp_path, f"tickers:\n          - {entry_lines.strip()}\n")

    with pytest.raises(ConfigError, match=missing_field):
        load_tracked_tickers(path)


def test_rejects_missing_tracked_tickers_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_tracked_tickers(tmp_path / "does-not-exist.yaml")


def test_empty_tracked_tickers_list_loads_as_none_configured(tmp_path):
    # Unlike funds, this whole file is optional, so an empty list means "none configured yet"
    # -- a copied-but-unfilled template must behave like an absent file, not break every
    # ingest and backfill command. A *missing* file still raises (above): only the CLI knows
    # the path is optional, and it checks for that before calling.
    path = write_tracked_tickers_yaml(tmp_path, "tickers: []\n")

    assert load_tracked_tickers(path) == []


def test_tracked_tickers_file_with_no_tickers_key_loads_as_none_configured(tmp_path):
    path = write_tracked_tickers_yaml(tmp_path, "# nothing uncommented yet\n")

    assert load_tracked_tickers(path) == []


def test_loads_exchange_with_string_suffix(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "London Stock Exchange"
            suffix: ".L"
        """,
    )

    suffixes = load_ticker_resolution(path).exchange_suffixes

    assert suffixes == {"London Stock Exchange": ".L"}


def test_loads_exchange_with_null_suffix_as_none(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
        """,
    )

    suffixes = load_ticker_resolution(path).exchange_suffixes

    assert suffixes == {"NASDAQ": None}


def test_loads_multiple_exchanges(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
          - exchange: "Tokyo Stock Exchange"
            suffix: ".T"
          - exchange: "Hong Kong Exchanges And Clearing Ltd"
            suffix: ".HK"
        """,
    )

    suffixes = load_ticker_resolution(path).exchange_suffixes

    assert suffixes == {
        "NASDAQ": None,
        "Tokyo Stock Exchange": ".T",
        "Hong Kong Exchanges And Clearing Ltd": ".HK",
    }


def test_rejects_missing_exchange_suffixes_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_ticker_resolution(tmp_path / "does-not-exist.yaml").exchange_suffixes


def test_rejects_empty_exchanges_list(tmp_path):
    path = write_exchange_suffixes_yaml(tmp_path, "exchanges: []\n")

    with pytest.raises(ConfigError, match="no exchanges"):
        load_ticker_resolution(path).exchange_suffixes


def test_rejects_exchange_entry_missing_exchange_field(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - suffix: ".L"
        """,
    )

    with pytest.raises(ConfigError, match="exchange"):
        load_ticker_resolution(path).exchange_suffixes


def test_rejects_exchange_entry_missing_suffix_key_entirely(tmp_path):
    # suffix must be explicitly present, even as null -- a key-less entry must not silently
    # default to "no suffix needed", since that's indistinguishable from a deliberate mapping.
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
        """,
    )

    with pytest.raises(ConfigError, match="suffix"):
        load_ticker_resolution(path).exchange_suffixes


def test_rejects_duplicate_exchange(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
          - exchange: "NASDAQ"
            suffix: ".XX"
        """,
    )

    with pytest.raises(ConfigError, match="(?i)duplicate"):
        load_ticker_resolution(path).exchange_suffixes


@pytest.mark.parametrize("bad_suffix", ["L", "", "DE", "123"])
def test_rejects_suffix_not_starting_with_dot(tmp_path, bad_suffix):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        f"""
        exchanges:
          - exchange: "London Stock Exchange"
            suffix: "{bad_suffix}"
        """,
    )

    with pytest.raises(ConfigError, match="Invalid suffix"):
        load_ticker_resolution(path).exchange_suffixes


def test_loads_ticker_overrides(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
        ticker_overrides:
          - raw_ticker: BRKB
            ticker: BRK-B
          - raw_ticker: HEIA
            ticker: HEIA.AS
        """,
    )

    overrides = load_ticker_resolution(path).ticker_overrides

    assert overrides == {"BRKB": "BRK-B", "HEIA": "HEIA.AS"}


def test_ticker_overrides_section_is_optional(tmp_path):
    # Unlike `exchanges:`, an absent or empty `ticker_overrides:` section is not an error --
    # most deployments won't have any known source-data mistags to override yet.
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
        """,
    )

    assert load_ticker_resolution(path).ticker_overrides == {}


def test_rejects_missing_ticker_overrides_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_ticker_resolution(tmp_path / "does-not-exist.yaml").ticker_overrides


def test_rejects_ticker_override_entry_missing_required_field(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
        ticker_overrides:
          - raw_ticker: BRKB
        """,
    )

    with pytest.raises(ConfigError, match="ticker"):
        load_ticker_resolution(path).ticker_overrides


def test_rejects_duplicate_ticker_override(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
        ticker_overrides:
          - raw_ticker: BRKB
            ticker: BRK-B
          - raw_ticker: BRKB
            ticker: BRK.B
        """,
    )

    with pytest.raises(ConfigError, match="(?i)duplicate"):
        load_ticker_resolution(path).ticker_overrides


def test_ticker_resolution_reads_both_sections_from_one_parse(tmp_path):
    # Exchange suffixes and ticker overrides are two views of the same file, always used
    # together. Loading them in one pass means they cannot disagree about the file's contents,
    # and a command stops re-reading and re-parsing the same YAML once per view.
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "London Stock Exchange"
            suffix: ".L"
          - exchange: "NASDAQ"
            suffix: null
        ticker_overrides:
          - raw_ticker: BRKB
            ticker: BRK-B
        """,
    )

    resolution = load_ticker_resolution(path)

    assert resolution.exchange_suffixes == {"London Stock Exchange": ".L", "NASDAQ": None}
    assert resolution.ticker_overrides == {"BRKB": "BRK-B"}


def test_ticker_resolution_reads_the_file_exactly_once(tmp_path, monkeypatch):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
        """,
    )
    opens = []
    real_open = Path.open
    monkeypatch.setattr(
        Path, "open", lambda self, *a, **k: opens.append(self) or real_open(self, *a, **k)
    )

    load_ticker_resolution(path)

    assert opens == [path]


def test_ticker_resolution_tolerates_a_missing_overrides_section(tmp_path):
    path = write_exchange_suffixes_yaml(
        tmp_path,
        """
        exchanges:
          - exchange: "NASDAQ"
            suffix: null
        """,
    )

    assert load_ticker_resolution(path).ticker_overrides == {}


def test_ticker_resolution_still_rejects_an_empty_exchanges_section(tmp_path):
    # exchanges: is required where ticker_overrides: is optional -- resolution cannot work
    # without a mapping, so an empty one is a config error, not "none configured".
    path = write_exchange_suffixes_yaml(tmp_path, "exchanges: []\n")

    with pytest.raises(ConfigError, match="no exchanges"):
        load_ticker_resolution(path)


def test_ticker_resolution_rejects_a_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_ticker_resolution(tmp_path / "nope.yaml")


# --- load_currencies ---------------------------------------------------------------------------


def test_loads_currencies_with_base(tmp_path):
    path = write_currencies_yaml(
        tmp_path,
        """
        base_currency: EUR
        currencies:
          - code: EUR
            enabled: true
          - code: USD
            enabled: true
          - code: GBP
            enabled: false
        """,
    )

    currencies = load_currencies(path)

    assert currencies.base_currency == "EUR"
    assert [c.code for c in currencies.currencies] == ["EUR", "USD", "GBP"]
    assert currencies.currencies[2].enabled is False


def test_rejects_missing_currencies_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_currencies(tmp_path / "does-not-exist.yaml")


def test_rejects_empty_currencies_list(tmp_path):
    path = write_currencies_yaml(tmp_path, "base_currency: EUR\ncurrencies: []\n")

    with pytest.raises(ConfigError, match="no currencies"):
        load_currencies(path)


def test_rejects_duplicate_currency_codes(tmp_path):
    path = write_currencies_yaml(
        tmp_path,
        """
        base_currency: EUR
        currencies:
          - code: EUR
            enabled: true
          - code: EUR
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="(?i)duplicate"):
        load_currencies(path)


@pytest.mark.parametrize("missing_field", ["code", "enabled"])
def test_rejects_currency_entry_missing_required_field(tmp_path, missing_field):
    fields = {"code": "EUR", "enabled": True}
    del fields[missing_field]
    entry_lines = "\n".join(f"            {k}: {_yaml_value(v)}" for k, v in fields.items())
    path = write_currencies_yaml(
        tmp_path, f"base_currency: EUR\ncurrencies:\n          - {entry_lines.strip()}\n"
    )

    with pytest.raises(ConfigError, match=missing_field):
        load_currencies(path)


def test_rejects_missing_base_currency_field(tmp_path):
    path = write_currencies_yaml(
        tmp_path,
        """
        currencies:
          - code: EUR
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="base_currency"):
        load_currencies(path)


def test_rejects_base_currency_not_present_in_currencies_list(tmp_path):
    path = write_currencies_yaml(
        tmp_path,
        """
        base_currency: USD
        currencies:
          - code: EUR
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="USD"):
        load_currencies(path)


def test_rejects_base_currency_not_enabled(tmp_path):
    path = write_currencies_yaml(
        tmp_path,
        """
        base_currency: EUR
        currencies:
          - code: EUR
            enabled: false
          - code: USD
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="(?i)not enabled"):
        load_currencies(path)


@pytest.mark.parametrize("bad_code", ["EU", "EURO", "eur", "12A"])
def test_rejects_currency_code_not_three_uppercase_letters(tmp_path, bad_code):
    path = write_currencies_yaml(
        tmp_path,
        f"""
        base_currency: {bad_code}
        currencies:
          - code: "{bad_code}"
            enabled: true
        """,
    )

    with pytest.raises(ConfigError, match="Invalid currency code"):
        load_currencies(path)
