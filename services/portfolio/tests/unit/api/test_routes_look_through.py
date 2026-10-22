"""POST /risk/look-through (RK-22): request validation, and the job's result passed through. The
last test stubs only the data-store client, so the job, the flow and the calculator run for
real and the JSON shape is checked whole."""

from datetime import date
from decimal import Decimal

import pytest
from portfolio import risk_jobs
from portfolio.datastore_client.base import (
    EtfConstituent,
    EtfFund,
    EtfHoldings,
    TickerInfo,
)
from portfolio.errors import DataStoreApiError


def _body(assets=None):
    if assets is None:
        assets = [{"ticker": "IWDA.AS", "weight": "60"}, {"ticker": "BRK-B", "weight": "40"}]
    return {"assets": assets}


_EMPTY = {"assets": [], "exposures": None}


def test_look_through_parses_the_mix_and_returns_the_jobs_result(monkeypatch, client):
    calls = []
    monkeypatch.setattr(risk_jobs, "get_look_through", lambda **kw: calls.append(kw) or _EMPTY)

    response = client.post("/risk/look-through", json=_body())

    assert response.status_code == 200
    assert response.json() == _EMPTY
    assert calls[0]["assets"] == [("IWDA.AS", Decimal("60")), ("BRK-B", Decimal("40"))]


@pytest.mark.parametrize(
    "body",
    [
        {"assets": []},
        {"assets": [{"ticker": "A", "weight": "1"}, {"ticker": "A", "weight": "2"}]},
        {"assets": [{"ticker": "A", "weight": "0"}]},
        {"assets": [{"ticker": "A" * 21, "weight": "1"}]},
        {"assets": [{"ticker": f"T{i}", "weight": "1"} for i in range(51)]},
    ],
    ids=["no assets", "duplicate ticker", "zero weight", "ticker too long", "too many assets"],
)
def test_look_through_rejects_an_invalid_request(monkeypatch, client, body):
    monkeypatch.setattr(risk_jobs, "get_look_through", lambda **kw: pytest.fail("job must not run"))

    assert client.post("/risk/look-through", json=body).status_code == 422


def test_a_failed_fund_list_raises_the_data_store_error(monkeypatch, client):
    # The bare test app has no handlers: app.py maps a DataStoreApiError to 502 for every route.
    class _Failing(_FakeDataStoreClient):
        def list_etf_funds(self):
            raise DataStoreApiError("boom")

    monkeypatch.setattr(
        risk_jobs, "_require_datastore_client", lambda settings, command: _Failing()
    )

    with pytest.raises(DataStoreApiError):
        client.post("/risk/look-through", json=_body())


class _FakeDataStoreClient:
    def get_ticker_info(self, ticker):
        return {
            "IWDA.AS": TickerInfo(True, True, None, "EUR", "EUR"),
            "BRK-B": TickerInfo(True, True, None, "USD", "USD"),
        }[ticker]

    def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        return []

    def list_etf_funds(self):
        return [EtfFund("world", "World", "IWDA.AS", True, date(2026, 8, 30))]

    def get_latest_etf_holdings(self, fund_key):
        return EtfHoldings(
            fund_key="world",
            ticker="IWDA.AS",
            as_of_date=date(2026, 8, 30),
            constituents=[
                EtfConstituent(
                    "BRKB",
                    "BRK-B",
                    "BERKSHIRE HATHAWAY INC CLASS B",
                    "Financials",
                    "Equity",
                    "United States",
                    "NYSE",
                    "USD",
                    Decimal("75"),
                ),
                EtfConstituent(
                    "SAP",
                    "SAP.DE",
                    "SAP",
                    "Information Technology",
                    "Equity",
                    "Germany",
                    "Xetra",
                    "EUR",
                    Decimal("25"),
                ),
            ],
        )


def _concentration(top1, top5, known, hhi, n):
    return {
        "top_shares": {"1": top1, "5": top5, "10": top5},
        "known_share": known,
        "herfindahl": hhi,
        "effective_n": n,
        "reason": None,
    }


def test_look_through_end_to_end(monkeypatch, client):
    monkeypatch.setattr(
        risk_jobs, "_require_datastore_client", lambda settings, command: _FakeDataStoreClient()
    )

    response = client.post("/risk/look-through", json=_body())

    assert response.status_code == 200
    # IWDA 0.6 x (75% BRK-B, 25% SAP) + BRK-B 0.4: BRK-B 0.85, SAP 0.15.
    two_buckets = _concentration(
        "0.85000000", "1.00000000", "1.00000000", "0.74500000", "1.34228188"
    )
    assert response.json() == {
        "assets": [
            {
                "ticker": "IWDA.AS",
                "status": "included",
                "reason": None,
                "kind": "look_through",
                "weight": "0.60000000",
                "matched_in": None,
                "note": None,
            },
            {
                "ticker": "BRK-B",
                "status": "included",
                "reason": None,
                "kind": "direct",
                "weight": "0.40000000",
                "matched_in": "IWDA.AS",
                "note": None,
            },
        ],
        "exposures": {
            "snapshots": [
                {
                    "ticker": "IWDA.AS",
                    "fund_key": "world",
                    "as_of_date": "2026-08-30",
                    "n_lines": 2,
                    "raw_weight_sum": "100.00000000",
                }
            ],
            "coverage": {
                "look_through": "0.60000000",
                "direct_matched": "0.40000000",
                "direct_unmatched": "0.00000000",
                "commodity": "0.00000000",
                "unmapped": "0.00000000",
            },
            "dimensions": {
                "currency": {
                    "label": "listing currency of holdings",
                    "buckets": [
                        {"label": "USD", "weight": "0.85000000", "kind": "known"},
                        {"label": "EUR", "weight": "0.15000000", "kind": "known"},
                    ],
                    "concentration": two_buckets,
                },
                "trading_currency": {
                    "label": "trading currency of positions",
                    "buckets": [
                        {"label": "EUR", "weight": "0.60000000", "kind": "known"},
                        {"label": "USD", "weight": "0.40000000", "kind": "known"},
                    ],
                    "concentration": _concentration(
                        "0.60000000", "1.00000000", "1.00000000", "0.52000000", "1.92307692"
                    ),
                },
                "sector": {
                    "label": "sector",
                    "buckets": [
                        {"label": "Financials", "weight": "0.85000000", "kind": "known"},
                        {
                            "label": "Information Technology",
                            "weight": "0.15000000",
                            "kind": "known",
                        },
                    ],
                    "concentration": two_buckets,
                },
                "country": {
                    "label": "country",
                    "buckets": [
                        {"label": "United States", "weight": "0.85000000", "kind": "known"},
                        {"label": "Germany", "weight": "0.15000000", "kind": "known"},
                    ],
                    "concentration": two_buckets,
                },
                "asset_class": {
                    "label": "asset class",
                    "buckets": [{"label": "Equity", "weight": "1.00000000", "kind": "known"}],
                    "concentration": _concentration(
                        "1.00000000", "1.00000000", "1.00000000", "1.00000000", "1.00000000"
                    ),
                },
            },
            "names": {
                "top": [
                    {
                        "issuer": "BERKSHIRE HATHAWAY INC",
                        "location": "United States",
                        "weight": "0.85000000",
                        "sources": ["BRK-B", "IWDA.AS"],
                    },
                    {
                        "issuer": "SAP",
                        "location": "Germany",
                        "weight": "0.15000000",
                        "sources": ["IWDA.AS"],
                    },
                ],
                "n_names": 2,
                "concentration": two_buckets,
            },
        },
    }
