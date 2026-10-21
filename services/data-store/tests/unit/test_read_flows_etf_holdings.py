import logging
from datetime import date
from decimal import Decimal

import pytest
from data_store.config_loader import FundConfig, TickerResolution
from data_store.read_flows import (
    FundNotConfigured,
    NoHoldingsSnapshot,
    etf_holdings_funds,
    latest_etf_holdings,
)


def make_fund(key, *, ticker=None, enabled=True, display_name=None):
    return FundConfig(
        key=key,
        display_name=display_name or key,
        provider="ishares",
        url="https://example.com/" + key,
        isin=None,
        price_source="yfinance",
        enabled=enabled,
        ticker=ticker,
    )


WORLD = make_fund("ishares_msci_world", ticker="IWDA.AS", display_name="iShares MSCI World")

RESOLUTION = TickerResolution(
    exchange_suffixes={
        "Hong Kong Exchanges And Clearing Ltd": ".HK",
        "London Stock Exchange": ".L",
        "NASDAQ": None,
    },
    ticker_overrides={"BRKB": "BRK-B"},
)


def line(ticker, name, asset_class, exchange, weight, *, currency="USD", sector="Tech"):
    """A row as get_latest_snapshot returns it."""
    return (ticker, name, sector, asset_class, "Somewhere", exchange, currency, Decimal(weight))


class FakeHoldingsRepo:
    def __init__(self, snapshots=None):
        """`snapshots` maps fund_key to (as_of_date, rows)."""
        self.snapshots = snapshots or {}

    def get_latest_snapshot(self, conn, fund_key):
        return self.snapshots.get(fund_key)

    def get_latest_as_of_dates(self, conn):
        return {key: as_of for key, (as_of, _) in self.snapshots.items()}


@pytest.fixture
def holdings(monkeypatch):
    import data_store.read_flows as read_flows

    def install(snapshots):
        monkeypatch.setattr(read_flows, "holdings_repository", FakeHoldingsRepo(snapshots))

    return install


def test_lists_every_configured_fund_with_its_ticker_and_latest_snapshot(holdings):
    holdings({"ishares_msci_world": (date(2026, 8, 30), [])})
    unscraped = make_fund("ishares_em", enabled=False)

    result = etf_holdings_funds(None, [WORLD, unscraped])

    assert result == {
        "funds": [
            {
                "fund_key": "ishares_msci_world",
                "display_name": "iShares MSCI World",
                "ticker": "IWDA.AS",
                "enabled": True,
                "latest_as_of_date": date(2026, 8, 30),
            },
            # Disabled and never scraped: still listed, so a caller can say why it has nothing.
            {
                "fund_key": "ishares_em",
                "display_name": "ishares_em",
                "ticker": None,
                "enabled": False,
                "latest_as_of_date": None,
            },
        ]
    }


def test_fund_list_logs_its_counts(holdings, caplog):
    caplog.set_level(logging.INFO)
    holdings({"ishares_msci_world": (date(2026, 8, 30), [])})

    etf_holdings_funds(None, [WORLD, make_fund("ishares_em")])

    assert "2 configured fund(s), 1 with a snapshot" in caplog.text


def test_serves_every_line_with_its_resolved_ticker(holdings):
    holdings(
        {
            "ishares_msci_world": (
                date(2026, 8, 30),
                [
                    line("AAPL", "APPLE INC", "Equity", "NASDAQ", "4.9100"),
                    line("BRKB", "BERKSHIRE HATHAWAY INC CLASS B", "Equity", "NASDAQ", "0.8000"),
                    line("BP.", "BP PLC", "Equity", "London Stock Exchange", "0.1200"),
                    line("700", "TENCENT", "Equity", "Hong Kong Exchanges And Clearing Ltd", "0.1"),
                    line(None, "USD CASH", "Cash", "-", "0.3800", sector="Cash and/or Derivatives"),
                ],
            )
        }
    )

    result = latest_etf_holdings(None, [WORLD], "ishares_msci_world", RESOLUTION)

    assert (result["fund_key"], result["ticker"], result["as_of_date"]) == (
        "ishares_msci_world",
        "IWDA.AS",
        date(2026, 8, 30),
    )
    # The resolved ticker is ingest's own answer: suffixes, normalizers and overrides alike.
    assert [(c["ticker"], c["yfinance_ticker"]) for c in result["constituents"]] == [
        ("AAPL", "AAPL"),
        ("BRKB", "BRK-B"),
        ("BP.", "BP.L"),
        ("700", "0700.HK"),
        (None, None),
    ]
    assert result["constituents"][4] == {
        "ticker": None,
        "yfinance_ticker": None,
        "name": "USD CASH",
        "sector": "Cash and/or Derivatives",
        "asset_class": "Cash",
        "location": "Somewhere",
        "exchange": "-",
        "currency": "USD",
        "weight_percent": Decimal("0.3800"),
    }


def test_only_an_equity_line_gets_a_resolved_ticker(holdings):
    # Ingest prices equity lines only, so a futures code is never a priced ticker, even when
    # its exchange is mapped.
    holdings(
        {
            "ishares_msci_world": (
                date(2026, 8, 30),
                [
                    line("ESU6", "S&P500 EMINI SEP 26", "Futures", "NASDAQ", "0"),
                    line("-", "EUR CASH", "Cash", "-", "0.0100"),
                ],
            )
        }
    )

    result = latest_etf_holdings(None, [WORLD], "ishares_msci_world", RESOLUTION)

    assert [(c["ticker"], c["yfinance_ticker"]) for c in result["constituents"]] == [
        ("ESU6", None),
        (None, None),  # "-" is the source's spelling of "no symbol"
    ]


def test_an_equity_line_on_an_unmapped_exchange_has_no_resolved_ticker(holdings):
    holdings(
        {
            "ishares_msci_world": (
                date(2026, 8, 30),
                [line("ABC", "ABC CORP", "Equity", "Some Unmapped Exchange", "0.0500")],
            )
        }
    )

    result = latest_etf_holdings(None, [WORLD], "ishares_msci_world", RESOLUTION)

    assert result["constituents"][0]["yfinance_ticker"] is None


def test_a_disabled_fund_still_serves_its_stored_snapshot(holdings):
    retired = make_fund("retired_fund", enabled=False)
    holdings({"retired_fund": (date(2025, 1, 3), [line("AAPL", "APPLE", "Equity", "NASDAQ", "1")])})

    result = latest_etf_holdings(None, [retired], "retired_fund", RESOLUTION)

    assert result["as_of_date"] == date(2025, 1, 3)


def test_an_unconfigured_fund_is_an_error(holdings, caplog):
    holdings({"stray_fund": (date(2026, 8, 30), [])})

    with pytest.raises(FundNotConfigured):
        latest_etf_holdings(None, [WORLD], "stray_fund", RESOLUTION)
    assert "not configured" in caplog.text


def test_a_fund_with_no_snapshot_is_an_error(holdings, caplog):
    holdings({})

    with pytest.raises(NoHoldingsSnapshot):
        latest_etf_holdings(None, [WORLD], "ishares_msci_world", RESOLUTION)
    assert "no holdings snapshot stored for ishares_msci_world" in caplog.text


def test_logs_a_summary_of_what_it_served(holdings, caplog):
    caplog.set_level(logging.INFO)
    holdings(
        {
            "ishares_msci_world": (
                date(2026, 8, 30),
                [
                    line("AAPL", "APPLE INC", "Equity", "NASDAQ", "60.0100"),
                    line("ABC", "ABC CORP", "Equity", "Some Unmapped Exchange", "40.0000"),
                    line(None, "USD CASH", "Cash", "-", "0.0200"),
                ],
            )
        }
    )

    latest_etf_holdings(None, [WORLD], "ishares_msci_world", RESOLUTION)

    assert "ishares_msci_world snapshot 2026-08-30" in caplog.text
    assert (
        "3 line(s), weights sum to 100.0300%; 2 equity line(s): 1 resolved, 1 unresolvable"
        in caplog.text
    )
