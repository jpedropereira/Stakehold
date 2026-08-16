import pytest
from data_store.config_loader import ConfigError, FundConfig, load_funds


def write_yaml(tmp_path, content):
    path = tmp_path / "etfs.yaml"
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
