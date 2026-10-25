"""risk_jobs.get_ranking (RK-29): builds the data-store client, runs risk/flows.compute_ranking,
logs its start and summary, and returns plain builtins the API's RankingResponse accepts. The
flow is tested in tests/unit/risk/test_flows_ranking.py and the figures in test_ranking.py;
this runs it for real on a fake client to check the shape. Small helpers are repeated per file
(no shared test modules).

Calendar: 2020-01-03 is a Friday. A, B, C and H are EUR, with three years of weekday returns from
a fixed seed; L starts in 2022, too late for 3Y. Every tracked ticker is ready.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from portfolio import risk_jobs
from portfolio.api.models import RankingResponse
from portfolio.datastore_client.base import (
    PricePoint,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
    TrackedTicker,
)
from portfolio.risk.windows import StandardWindow
from portfolio.settings import SettingsError

FIRST = date(2020, 1, 3)
LATE = date(2022, 1, 7)
END = date(2023, 3, 1)
_SEEDS = {"A": 1, "B": 2, "C": 3, "H": 4, "L": 5}


def _settings(
    datastore_api_url="http://datastore:8000",
    internal_api_secret="secret",  # pragma: allowlist secret
):
    return type(
        "S",
        (),
        {
            "database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "admin_database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "datastore_api_url": datastore_api_url,
            "internal_api_secret": internal_api_secret,  # pragma: allowlist secret
        },
    )()


def _prices(ticker):
    first = LATE if ticker == "L" else FIRST
    days, day = [], first
    while day < END:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    returns = np.random.default_rng(_SEEDS[ticker]).normal(0.0003, 0.01, len(days))
    return [PricePoint(first, Decimal("100"), None)] + [
        PricePoint(d, Decimal("100"), Decimal(str(round(float(r), 6))))
        for d, r in zip(days, returns)
    ]


def _tracked(ticker):
    return TrackedTicker(
        ticker=ticker,
        display_name=f"{ticker} name",
        asset_type="etf",
        currency="EUR",
        exchange=None,
        origin="config",
        status="ready",
        enabled=True,
        attempts=0,
        last_error=None,
        requested_by=None,
        requested_at="2024-01-01T00:00:00+00:00",
        ready_at=None,
    )


class _FakeDataStoreClient:
    tracked = ("A", "B", "C", "H", "L")

    def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        return [_tracked(t) for t in self.tracked]

    def get_ticker_info(self, ticker):
        return TickerInfo(
            exists=True, has_returns=True, status="ready", currency="EUR", fx_currency="EUR"
        )

    def get_price_series(self, ticker, start, end):
        return _prices(ticker)

    def get_fx_rate_series(self, base, quote, start, end):
        raise AssertionError("a EUR-only mix needs no FX")

    def get_risk_free_series(self, start, end):
        point = RiskFreePoint(date(2019, 12, 2), Decimal("-0.5000"), "estr")
        return RiskFreeSeries(first_date=date(1999, 1, 4), points=[point])


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeDataStoreClient()
    monkeypatch.setattr(risk_jobs, "load_settings", _settings)
    monkeypatch.setattr(risk_jobs, "_require_datastore_client", lambda settings, command: client)
    return client


def _get(assets=(("A", "3"), ("B", "1")), weight="0.05", **kw):
    kw.setdefault("window", StandardWindow.Y3)
    return risk_jobs.get_ranking(
        assets=[(t, Decimal(w)) for t, w in assets], weight=Decimal(weight), **kw
    )


def test_get_ranking_returns_builtins_the_response_model_accepts(fake_client):
    result = _get()

    RankingResponse.model_validate(result)
    assert result["label"] == "ex-ante · constant weights"
    assert result["rf_available"] is True
    assert [s["status"] for s in result["statuses"]] == ["included", "included"]
    assert result["nothing"] is None
    assert (result["universe"], result["cap"], result["over_cap"]) == (3, 50, [])
    assert (result["weight"], result["rank_by"]) == (pytest.approx(0.05), "sigma_p")
    assert result["window"]["label"] == "3Y"

    ranked = result["ranked"]
    assert sorted(row["ticker"] for row in ranked) == ["C", "H"]
    deltas = [row["sigma_p"]["delta"] for row in ranked]
    assert deltas == sorted(deltas)
    first = ranked[0]
    assert first["name"] == f"{first['ticker']} name"
    assert first["history_start"] == FIRST
    assert first["n_weeks"] > 156
    assert list(first) == [
        "ticker",
        "name",
        "history_start",
        "n_weeks",
        "sigma_p",
        "diversification_ratio",
        "candidate_pcr",
        "candidate_correlation",
        "sharpe",
    ]
    assert first["sigma_p"]["after"]["frequency"] == "weekly"
    assert first["candidate_pcr"]["before"] is None
    assert first["sharpe"]["delta_se"] > 0
    assert result["greyed"] == []

    (late,) = result["unranked"]
    assert (late["ticker"], late["name"], late["status"]) == ("L", "L name", "short_history")
    assert late["history_start"] == LATE
    assert late["reason"].startswith("insufficient history (have ")


def test_get_ranking_by_diversification_ratio(fake_client):
    result = _get(rank_by="diversification_ratio")

    RankingResponse.model_validate(result)
    deltas = [row["diversification_ratio"]["delta"] for row in result["ranked"]]
    assert deltas == sorted(deltas, reverse=True)


def test_get_ranking_with_nothing_to_rank_has_only_statuses(fake_client):
    fake_client.tracked = ("A", "B")
    result = _get()

    RankingResponse.model_validate(result)
    assert result["nothing"] == "no tracked ticker outside the mix to rank"
    for key in ("window", "ranked", "greyed"):
        assert result[key] is None
    assert result["unranked"] == []


def test_get_ranking_logs_a_start_line_and_a_summary(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        _get()

    assert "get-ranking: ranking candidates against 2 asset(s) at 5.0%." in caplog.text
    assert (
        "get-ranking: done, 2 of 2 asset(s) included, 2 ranked, 0 greyed, 1 not ranked, "
        "0 past the cap, window 3Y, risk-free rate available." in caplog.text
    )


def test_get_ranking_warns_when_there_is_nothing_to_rank(fake_client, caplog):
    fake_client.tracked = ()
    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        _get()

    warning = next(r for r in caplog.records if "get-ranking: done" in r.getMessage())
    assert warning.levelno == logging.WARNING
    assert "nothing to rank: no tracked ticker outside the mix to rank" in warning.getMessage()


def test_get_ranking_needs_data_store_settings(monkeypatch):
    monkeypatch.setattr(risk_jobs, "load_settings", lambda: _settings(datastore_api_url=None))

    with pytest.raises(SettingsError, match="a ranking"):
        _get()
