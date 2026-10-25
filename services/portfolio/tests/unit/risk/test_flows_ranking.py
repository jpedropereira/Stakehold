"""risk/flows.py's ranking flow (RK-29): pick the candidates from data-store's tracked tickers,
fetch them with the holdings in one go, and hand them to risk/ranking.py, whose figures are
tested in test_ranking.py.

- The universe is every enabled `ready` tracked ticker that isn't in the mix (excluded holdings
  included), alphabetically, capped at RANKING_CAP; those past the cap are named, not fetched.
- A candidate the fetch leaves out is listed with its status and reason, never an error.
- Nothing to rank (no tracked candidate, none measurable, or no holding) is said so, with no
  risk-free fetch.

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
    TrackedTicker,
)
from portfolio.errors import DataStoreApiError
from portfolio.risk import flows
from portfolio.risk.ranking import DIVERSIFICATION_RATIO, SIGMA_P
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


def _tracked(ticker, status="ready", enabled=True):
    return TrackedTicker(
        ticker=ticker,
        display_name=f"{ticker} name",
        asset_type="etf",
        currency="EUR",
        exchange=None,
        origin="config",
        status=status,
        enabled=enabled,
        attempts=0,
        last_error=None,
        requested_by=None,
        requested_at="2024-01-01T00:00:00+00:00",
        ready_at=None,
    )


class _FakeDataStoreClient:
    """`tickers` maps a ticker to (TickerInfo, prices or an exception to raise); `tracked` is
    the tracked-ticker list, or an exception to raise."""

    def __init__(self, tickers, tracked, risk_free=RF):
        self._tickers = tickers
        self._tracked = tracked
        self._risk_free = risk_free
        self.tracked_calls = []
        self.risk_free_calls = []
        self.price_calls = []

    def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.tracked_calls.append(statuses)
        if isinstance(self._tracked, Exception):
            raise self._tracked
        return [row for row in self._tracked if statuses is None or row.status in statuses]

    def get_ticker_info(self, ticker):
        return self._tickers[ticker][0]

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


def _client(tracked=("C", "D"), **extra):
    tickers = {
        "A": (TRACKED, _prices(1)),
        "B": (TRACKED, _prices(2)),
        "C": (TRACKED, _prices(3)),
        "D": (TRACKED, _prices(4)),
        **extra,
    }
    rows = [_tracked(t) if isinstance(t, str) else t for t in ("A", "B", *tracked)]
    return _FakeDataStoreClient(tickers, rows)


def _run(client, mix=(("A", "3"), ("B", "1")), weight=0.05, **kw):
    kw.setdefault("window", StandardWindow.Y3)
    return flows.compute_ranking(
        client, [(t, Decimal(w)) for t, w in mix], weight, today=TODAY, **kw
    )


def _scored(result):
    return [row.ticker for row in result.report.ranked]


def _unranked(result):
    return [(u.ticker, u.status, u.reason) for u in result.unranked]


# --- the universe --------------------------------------------------------------------------------


def test_ranks_every_ready_enabled_tracked_ticker_not_in_the_mix():
    client = _client(
        tracked=(
            "C",
            "D",
            _tracked("P", status="pending"),
            _tracked("F", status="failed"),
            _tracked("OFF", enabled=False),
        )
    )
    result = _run(client)

    assert client.tracked_calls == [["ready"]]
    assert sorted(_scored(result)) == ["C", "D"]
    assert result.universe == 2
    assert result.over_cap == []
    assert sorted(client.price_calls) == ["A", "B", "C", "D"]
    assert result.names == {"C": "C name", "D": "D name"}


def test_an_excluded_holding_is_never_a_candidate():
    result = _run(_client(), mix=[("A", "3"), ("B", "1"), ("C", "1")], exclude={"C"})
    assert _scored(result) == ["D"]
    assert result.weights == pytest.approx({"A": 0.75, "B": 0.25})


def test_past_the_cap_candidates_are_named_and_not_fetched(monkeypatch):
    monkeypatch.setattr(flows, "RANKING_CAP", 2)
    extra = {t: (TRACKED, _prices(seed)) for seed, t in enumerate(("E", "G"), start=5)}
    client = _client(tracked=("G", "C", "E", "D"), **extra)
    result = _run(client)

    assert sorted(_scored(result)) == ["C", "D"]
    assert (result.universe, result.over_cap) == (4, ["E", "G"])
    assert "E" not in client.price_calls and "G" not in client.price_calls


def test_the_cap_is_fifty():
    assert flows.RANKING_CAP == 50


# --- what isn't ranked ---------------------------------------------------------------------------


def test_a_candidate_the_fetch_leaves_out_is_listed_with_its_reason():
    client = _client(tracked=("C", "D"), D=(TRACKED, DataStoreApiError("boom")))
    result = _run(client)

    assert _scored(result) == ["C"]
    assert _unranked(result) == [("D", "failed", "couldn't reach data-store")]


def test_a_candidate_too_young_for_the_window_is_listed_with_its_weeks():
    late = date(2020, 6, 5)  # a Friday, under 2 years before TODAY
    client = _client(D=(TRACKED, _prices(4, first=late)))
    result = _run(client)

    assert _scored(result) == ["C"]
    ((ticker, status, reason),) = _unranked(result)
    assert (ticker, status) == ("D", "short_history")
    assert reason.startswith("insufficient history (have ")
    (short,) = result.unranked
    assert short.history_start == late
    assert short.n_weeks is not None


def test_no_tracked_candidate_is_nothing_to_rank():
    client = _client(tracked=())
    result = _run(client)

    assert result.report is None
    assert result.nothing == "no tracked ticker outside the mix to rank"
    assert [(s.status, s.reason) for s in result.statuses] == [
        ("not_fetched", "not fetched: nothing to measure")
    ] * 2
    assert client.price_calls == []
    assert client.risk_free_calls == []


def test_no_measurable_candidate_is_nothing_to_rank():
    client = _client(tracked=("C",), C=(TRACKED, DataStoreApiError("boom")))
    result = _run(client)

    assert result.report is None
    assert result.nothing == "no candidate could be measured"
    assert _unranked(result) == [("C", "failed", "couldn't reach data-store")]
    assert client.risk_free_calls == []


def test_no_included_holding_is_nothing_to_rank():
    client = _client(A=(TRACKED, DataStoreApiError("boom")), B=(TRACKED, DataStoreApiError("x")))
    result = _run(client)

    assert result.report is None
    assert result.nothing == "no holding could be included"
    assert [s.status for s in result.statuses] == ["failed", "failed"]
    assert client.risk_free_calls == []


def test_a_failing_tracked_list_raises():
    client = _FakeDataStoreClient({}, DataStoreApiError("down"))
    with pytest.raises(DataStoreApiError):
        _run(client)


# --- the request ---------------------------------------------------------------------------------


def test_the_order_and_weight_reach_the_report():
    result = _run(_client(), weight=0.1, rank_by=DIVERSIFICATION_RATIO)
    assert (result.report.weight, result.report.rank_by) == (0.1, DIVERSIFICATION_RATIO)
    assert _run(_client()).report.rank_by == SIGMA_P


def test_one_risk_free_fetch_from_two_weeks_before_the_oldest_series():
    client = _client()
    result = _run(client)
    assert result.rf_available is True
    assert client.risk_free_calls == [(FIRST - timedelta(days=14), TODAY)]


def test_without_a_risk_free_series_the_ranking_still_runs():
    client = _client()
    client._risk_free = None
    result = _run(client)
    assert result.rf_available is False
    assert sorted(_scored(result)) == ["C", "D"]


# --- logging -------------------------------------------------------------------------------------


def test_logs_a_start_line_each_candidate_and_a_summary(caplog):
    client = _client(
        tracked=("C", "D", "L"),
        D=(TRACKED, DataStoreApiError("boom")),
        L=(TRACKED, _prices(5, first=date(2020, 6, 5))),
    )
    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        _run(client)

    text = caplog.text
    assert (
        "ranking: 2 holding(s), 0 excluded by request, 3 tracked candidate(s), 0 past the cap "
        "of 50, at 5.0%, window 3Y, ranked by sigma_p." in text
    )
    assert "ranking: C: delta sigma_p " in text
    assert "ranking: L: not ranked, insufficient history (have " in text
    assert "ranking: D: not ranked, couldn't reach data-store." in text
    assert "ranking: done, 1 ranked, 0 greyed, 2 not ranked, 0 past the cap." in text


def test_logs_nothing_to_rank_as_such(caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        _run(_client(tracked=()))
    assert "ranking: nothing to rank: no tracked ticker outside the mix to rank." in caplog.text
