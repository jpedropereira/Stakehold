from datetime import date
from decimal import Decimal

from data_store import jobs
from data_store.read_flows import FundNotConfigured, NoHoldingsSnapshot


def test_lists_the_configured_funds(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_etf_holdings_funds",
        lambda: {
            "funds": [
                {
                    "fund_key": "ishares_msci_world",
                    "display_name": "iShares MSCI World UCITS ETF (Acc)",
                    "ticker": "IWDA.AS",
                    "enabled": True,
                    "latest_as_of_date": date(2026, 8, 30),
                },
                {
                    "fund_key": "ishares_em",
                    "display_name": "iShares EM",
                    "ticker": None,
                    "enabled": False,
                    "latest_as_of_date": None,
                },
            ]
        },
    )

    response = client.get("/etf-holdings")

    assert response.status_code == 200
    assert response.json() == {
        "funds": [
            {
                "fund_key": "ishares_msci_world",
                "display_name": "iShares MSCI World UCITS ETF (Acc)",
                "ticker": "IWDA.AS",
                "enabled": True,
                "latest_as_of_date": "2026-08-30",
            },
            {
                "fund_key": "ishares_em",
                "display_name": "iShares EM",
                "ticker": None,
                "enabled": False,
                "latest_as_of_date": None,
            },
        ]
    }


def test_returns_the_latest_snapshot(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_latest_etf_holdings",
        lambda **kw: calls.append(kw)
        or {
            "fund_key": "ishares_msci_world",
            "ticker": "IWDA.AS",
            "as_of_date": date(2026, 8, 30),
            "constituents": [
                {
                    "ticker": "700",
                    "yfinance_ticker": "0700.HK",
                    "name": "TENCENT HOLDINGS LTD",
                    "sector": "Communication",
                    "asset_class": "Equity",
                    "location": "China",
                    "exchange": "Hong Kong Exchanges And Clearing Ltd",
                    "currency": "HKD",
                    "weight_percent": Decimal("0.3100"),
                },
                {
                    "ticker": None,
                    "yfinance_ticker": None,
                    "name": "USD CASH",
                    "sector": "Cash and/or Derivatives",
                    "asset_class": "Cash",
                    "location": "United States",
                    "exchange": "-",
                    "currency": "USD",
                    "weight_percent": Decimal("0.3800"),
                },
            ],
        },
    )

    response = client.get("/etf-holdings/ishares_msci_world/latest")

    assert response.status_code == 200
    assert calls == [{"fund_key": "ishares_msci_world"}]
    body = response.json()
    assert (body["fund_key"], body["ticker"], body["as_of_date"]) == (
        "ishares_msci_world",
        "IWDA.AS",
        "2026-08-30",
    )
    assert body["constituents"][0] == {
        "ticker": "700",
        "yfinance_ticker": "0700.HK",
        "name": "TENCENT HOLDINGS LTD",
        "sector": "Communication",
        "asset_class": "Equity",
        "location": "China",
        "exchange": "Hong Kong Exchanges And Clearing Ltd",
        "currency": "HKD",
        # A string, like every other read model: the NUMERIC scale survives the round trip.
        "weight_percent": "0.3100",
    }
    assert body["constituents"][1]["ticker"] is None


def test_404s_for_an_unconfigured_fund(monkeypatch, client):
    def raise_(**kw):
        raise FundNotConfigured(kw["fund_key"])

    monkeypatch.setattr(jobs, "get_latest_etf_holdings", raise_)

    response = client.get("/etf-holdings/no_such_fund/latest")

    assert response.status_code == 404
    assert response.json() == {"detail": "fund not configured: no_such_fund"}


def test_404s_for_a_fund_with_no_snapshot(monkeypatch, client):
    def raise_(**kw):
        raise NoHoldingsSnapshot(kw["fund_key"])

    monkeypatch.setattr(jobs, "get_latest_etf_holdings", raise_)

    response = client.get("/etf-holdings/ishares_em/latest")

    assert response.status_code == 404
    assert response.json() == {"detail": "no holdings snapshot stored: ishares_em"}


def test_rejects_a_fund_key_longer_than_the_column(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_latest_etf_holdings", lambda **kw: {})

    response = client.get("/etf-holdings/" + "x" * 51 + "/latest")

    assert response.status_code == 422
