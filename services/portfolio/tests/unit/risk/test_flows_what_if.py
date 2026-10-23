"""risk/flows.py's what-if flow (RK-19): fetch the holdings and the candidate in one go, give
every holding and the candidate a status, normalise the holdings' weights over those included,
and hand the rest to risk/what_if.py, whose figures are tested in test_what_if.py.

- An unmeasurable candidate, no included holding, or an unmeasurable replaced holding means no
  report, with the reason, never an error.
- R14: a candidate data-store prices only through a fund's holdings (`TickerInfo.status` None)
  is flagged `survivorship`; a tracked one isn't.

Calendar: 2018-01-05 is a Friday. Every ticker's prices are the weekdays after its first date,
with returns from a fixed seed. Small helpers are repeated per file (no shared test modules).
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from portfolio.datastore_client.base import (
    PricePoint,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
from portfolio.errors import DataStoreApiError
from portfolio.risk import flows
from portfolio.risk.types import Flag
from portfolio.risk.what_if import PRO_RATA, Funding
from portfolio.risk.windows import StandardWindow

FIRST = date(2018, 1, 5)
TODAY = date(2022, 3, 1)
RF = RiskFreeSeries(
    first_date=date(1999, 1, 4),
    points=[RiskFreePoint(date(2017, 12, 1), Decimal("-0.5000"), "estr")],
)
TRACKED = TickerInfo(
    exists=True, has_returns=True, status="ready", currency="EUR", fx_currency="EUR"
)
# Priced only through a fund's holdings: data-store reports no onboarding status.
CONSTITUENT = TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
USD = TickerInfo(exists=True, has_returns=True, status="ready", currency="USD", fx_currency="USD")
UNTRACKED = TickerInfo(exists=False, has_returns=False)


def _weekdays(first, end=TODAY):
    days, day = [], first
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days


def _prices(seed, first=FIRST):
    days = _weekdays(first)
    returns = np.random.default_rng(seed).normal(0.0003, 0.01, len(days))
    return [PricePoint(first, Decimal("100"), None)] + [
        PricePoint(d, Decimal("100"), Decimal(str(round(float(r), 6))))
        for d, r in zip(days, returns)
    ]


class _FakeDataStoreClient:
    """`tickers` maps a ticker to (TickerInfo, prices or an exception to raise)."""

    def __init__(self, tickers, risk_free=RF):
        self._tickers = tickers
        self._risk_free = risk_free
        self.risk_free_calls = []
        self.price_calls = []

    def get_ticker_info(self, ticker):
        return self._tickers.get(ticker, (UNTRACKED, None))[0]

    def get_price_series(self, ticker, start, end):
        self.price_calls.append(ticker)
        prices = self._tickers[ticker][1]
        if isinstance(prices, Exception):
            raise prices
        return prices

    def get_fx_rate_series(self, base, quote, start, end):
        return []

    def get_risk_free_series(self, start, end):
        self.risk_free_calls.append((start, end))
        return self._risk_free


def _client(**extra):
    return _FakeDataStoreClient(
        {
            "A": (TRACKED, _prices(1)),
            "B": (TRACKED, _prices(2)),
            "C": (TRACKED, _prices(3)),
            **extra,
        }
    )


def _run(client, mix=(("A", "3"), ("B", "1")), candidate="C", weight=0.2, funding=PRO_RATA, **kw):
    kw.setdefault("window", StandardWindow.Y3)
    return flows.compute_what_if(
        client,
        [(t, Decimal(w)) for t, w in mix],
        candidate,
        weight,
        funding,
        today=TODAY,
        **kw,
    )


def _statuses(result):
    return [(s.ticker, s.status, s.reason) for s in result.statuses]


def test_the_holdings_are_normalised_then_funded_pro_rata():
    result = _run(
        _client(X=(TRACKED, _prices(4))), mix=[("A", "3"), ("X", "1"), ("B", "1")], exclude={"X"}
    )

    assert _statuses(result) == [
        ("A", "included", None),
        ("X", "excluded", "excluded by request"),
        ("B", "included", None),
    ]
    assert (result.candidate.ticker, result.candidate.status) == ("C", "included")
    assert result.weights == pytest.approx({"A": 0.75, "B": 0.25})
    assert result.report.funded.after == pytest.approx({"A": 0.6, "B": 0.2, "C": 0.2})
    assert result.funding_reason is None
    assert result.rf_available is True


def test_one_fetch_covers_the_holdings_and_the_candidate():
    client = _client()
    _run(client)
    assert sorted(client.price_calls) == ["A", "B", "C"]
    assert len(client.risk_free_calls) == 1


def test_replace_moves_the_weight_from_the_named_holding():
    result = _run(_client(), funding=Funding.replace("B"))
    assert result.report.funded.after == pytest.approx({"A": 0.75, "B": 0.05, "C": 0.2})


@pytest.mark.parametrize(
    "info, prices, status, reason",
    [
        (UNTRACKED, None, "not_tracked", "not tracked"),
        (USD, _prices(3), "no_fx_rate", "no FX series for USD"),
        (TRACKED, DataStoreApiError("boom"), "failed", "couldn't reach data-store"),
    ],
    ids=["not tracked", "no fx", "failed"],
)
def test_an_unmeasurable_candidate_means_no_report_and_says_why(
    caplog, info, prices, status, reason
):
    client = _client(C=(info, prices))
    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client)

    assert (result.candidate.status, result.candidate.reason) == (status, reason)
    assert result.report is None
    assert client.risk_free_calls == []
    assert f"what-if: the candidate C can't be measured ({reason}), so nothing to compare" in (
        caplog.text
    )


def test_no_included_holding_means_no_report():
    client = _client(A=(UNTRACKED, None), B=(UNTRACKED, None))
    result = _run(client)
    assert [s.status for s in result.statuses] == ["not_tracked", "not_tracked"]
    assert result.candidate.status == "included"
    assert result.report is None
    assert client.risk_free_calls == []


def test_an_unmeasurable_replaced_holding_means_no_report_and_says_why():
    result = _run(_client(B=(USD, _prices(2))), funding=Funding.replace("B"))
    assert result.report is None
    assert (
        result.funding_reason
        == "B, the holding to replace, can't be measured: no FX series for USD"
    )


def test_a_constituent_only_candidate_is_flagged_survivorship():
    result = _run(_client(C=(CONSTITUENT, _prices(3))))
    assert result.survivorship is True
    assert Flag.SURVIVORSHIP in result.report.sigma_p.after.flags


def test_a_tracked_candidate_is_not_flagged_even_when_a_holding_is_a_constituent():
    result = _run(_client(A=(CONSTITUENT, _prices(1))))
    assert result.survivorship is False
    assert Flag.SURVIVORSHIP not in result.report.sigma_p.after.flags


def test_logs_a_start_line_the_window_and_a_summary(caplog):
    late = date(2019, 12, 2)  # about 117 weeks before TODAY: shorter than 3Y, over 104 weeks
    client = _client(C=(TRACKED, _prices(3, first=late)))
    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client)

    assert result.report.shortened is True
    text = caplog.text
    assert (
        "what-if: 2 holding(s), 0 excluded by request, candidate C at 20.0%, funded pro_rata, "
        "window 3Y." in text
    )
    assert "what-if: window 3Y: measured " in text
    assert "shortened by the candidate C (the holdings alone: " in text
    assert "what-if: done, 2 holding(s) included, 0 left out; delta sigma_p " in text
    assert "; threshold: candidate Sharpe " in text
    assert "survivorship not flagged." in text


def test_the_summary_says_why_the_threshold_greys_on_a_short_window(caplog):
    client = _client(C=(TRACKED, _prices(3, first=date(2020, 3, 2))))
    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        _run(client)
    assert "threshold not shown (insufficient history (have 103, need 104))" in caplog.text


def test_the_summary_says_the_threshold_isnt_shown_under_replace(caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        _run(_client(), funding=Funding.replace("B"))
    assert "threshold not shown (shown under pro-rata funding only)" in caplog.text
