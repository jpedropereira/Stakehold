"""analytics_jobs.get_look_through: runs look_through/flows.py, logs its summary, and returns plain
builtins the API's LookThroughResponse accepts. Small helpers are repeated here rather than
shared (no test packages: see CLAUDE.md)."""

import logging
from datetime import date
from decimal import Decimal

from portfolio import analytics_jobs
from portfolio.api.models import LookThroughResponse
from portfolio.datastore_client.base import (
    EtfConstituent,
    EtfFund,
    EtfHoldings,
    TickerInfo,
)


class _FakeDataStoreClient:
    def get_ticker_info(self, ticker):
        if ticker == "NOPE":
            return TickerInfo(False, False)
        return TickerInfo(True, True, None, "USD", "USD")

    def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        return []

    def list_etf_funds(self):
        return [EtfFund("world", "World", "W.AS", True, date(2026, 8, 30))]

    def get_latest_etf_holdings(self, fund_key):
        line = EtfConstituent(
            "AAA", "AAA", "AAA", "Tech", "Equity", "United States", "X", "USD", Decimal("100")
        )
        return EtfHoldings("world", "W.AS", date(2026, 8, 30), [line])


def _run(monkeypatch, assets):
    monkeypatch.setattr(
        analytics_jobs, "require_datastore_client", lambda settings, command: _FakeDataStoreClient()
    )
    return analytics_jobs.get_look_through(assets=assets, settings=object())


def test_the_summary_line_names_each_share_of_the_mix(monkeypatch, caplog):
    assets = [
        ("W.AS", Decimal("5")),
        ("AAA", Decimal("2")),
        ("BABA", Decimal("2")),
        ("EGLN.L", Decimal("1")),
        ("NOPE", Decimal("1")),
    ]
    with caplog.at_level(logging.INFO, logger="portfolio.analytics_jobs"):
        _run(monkeypatch, assets)
    assert caplog.messages[-1] == (
        "get-look-through: done: 50.0% looked through, 20.0% direct and matched in a fund, "
        "20.0% direct in no fund, 10.0% commodity, 0.0% unmapped; 1 snapshot(s), 3 name(s), "
        "1 asset(s) left out."
    )


def test_nothing_included_says_so(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.analytics_jobs"):
        result = _run(monkeypatch, [("NOPE", Decimal("1"))])
    assert result["exposures"] is None
    assert caplog.records[-1].levelno == logging.WARNING
    assert caplog.messages[-1] == (
        "get-look-through: done, no asset could be included (1 requested), so there are no "
        "exposures."
    )


def test_the_result_is_what_the_response_model_accepts(monkeypatch):
    result = _run(monkeypatch, [("W.AS", Decimal("1")), ("NOPE", Decimal("1"))])
    response = LookThroughResponse.model_validate(result)
    assert response.exposures.names.top[0].issuer == "AAA"
    assert response.assets[1].status == "not_tracked"
