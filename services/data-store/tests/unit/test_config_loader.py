import pytest
from data_store.config_loader import (
    ConfigError,
    FundConfig,
    TickerConfig,
    load_funds,
    load_tracked_tickers,
)


def write_yaml(tmp_path, content):
    path = tmp_path / "etfs.yaml"
    path.write_text(content)
    return path


def write_tracked_tickers_yaml(tmp_path, content):
    path = tmp_path / "tracked_tickers.yaml"
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


def test_rejects_empty_tracked_tickers_list(tmp_path):
    path = write_tracked_tickers_yaml(tmp_path, "tickers: []\n")

    with pytest.raises(ConfigError, match="no tickers"):
        load_tracked_tickers(path)
