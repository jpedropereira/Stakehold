"""risk_jobs.get_ex_ante_risk (RK-17): builds the data-store client, runs
risk/flows.compute_ex_ante, logs its start and summary, and returns plain builtins the API's
ExAnteRiskResponse accepts. The
flow is tested in tests/unit/risk/test_flows_ex_ante.py; this runs it for real on a fake client
to check the shape. Small helpers are repeated per file (no shared test modules).

Calendar: 2020-01-03 is a Friday. A and B are EUR, with three years of weekday returns from a
fixed seed; VWCE.DE is the benchmark. Anything else is untracked.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from portfolio import risk_jobs
from portfolio.api.models import ExAnteRiskResponse
from portfolio.datastore_client.base import (
    PricePoint,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
from portfolio.risk.windows import StandardWindow
from portfolio.settings import SettingsError

FIRST = date(2020, 1, 3)
END = date(2023, 3, 1)
_SEEDS = {"A": 1, "B": 2, "VWCE.DE": 3}


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


def _prices(seed):
    days, day = [], FIRST
    while day < END:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    returns = np.random.default_rng(seed).normal(0.0003, 0.01, len(days))
    return [PricePoint(FIRST, Decimal("100"), None)] + [
        PricePoint(d, Decimal("100"), Decimal(str(round(float(r), 6))))
        for d, r in zip(days, returns)
    ]


class _FakeDataStoreClient:
    def get_ticker_info(self, ticker):
        if ticker in _SEEDS:
            return TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
        return TickerInfo(exists=False, has_returns=False)

    def get_price_series(self, ticker, start, end):
        return _prices(_SEEDS[ticker])

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


def _get(assets, **kw):
    return risk_jobs.get_ex_ante_risk(assets=[(t, Decimal(w)) for t, w in assets], **kw)


def test_get_ex_ante_risk_returns_builtins_the_response_model_accepts(fake_client):
    result = _get([("A", "3"), ("B", "1"), ("X", "1")], window=StandardWindow.Y1)

    ExAnteRiskResponse.model_validate(result)
    assert result["label"] == "ex-ante · constant weights"
    assert result["currency"] == "eur"
    assert result["rf_available"] is True
    assert result["statuses"] == [
        {"ticker": "A", "status": "included", "reason": None},
        {"ticker": "B", "status": "included", "reason": None},
        {"ticker": "X", "status": "not_tracked", "reason": "not tracked"},
    ]
    window = result["window"]
    assert window["label"] == "1Y"
    assert window["limited_by"] == []
    assert window["start"] <= window["requested_start"]  # the Friday its first week starts
    assert result["sigma_p"]["frequency"] == "weekly"
    assert result["sigma_p"]["value"] is not None
    assert result["diversification_ratio"]["value"] is None  # under 104 weeks
    assert result["effective_n"] == pytest.approx(1 / (0.75**2 + 0.25**2))
    assert [(h["ticker"], h["weight"]) for h in result["holdings"]] == [
        ("A", pytest.approx(0.75)),
        ("B", pytest.approx(0.25)),
    ]
    cw = result["constant_weight"]
    assert cw["volatility"]["value"] == pytest.approx(result["sigma_p"]["value"], abs=1e-12)
    assert cw["var_95"]["flags"] == ["nonsync_daily"]
    assert "peak_date" in cw and "max_days_under_water" in cw
    correlation = result["correlation"]
    assert correlation["keys"] == ["A", "B", "VWCE.DE"]
    assert correlation["benchmark_key"] == "VWCE.DE"
    assert [(c["a"], c["b"]) for c in correlation["cells"]] == [
        ("A", "A"),
        ("A", "B"),
        ("A", "VWCE.DE"),
        ("B", "B"),
        ("B", "VWCE.DE"),
        ("VWCE.DE", "VWCE.DE"),
    ]
    assert result["benchmark"]["first_date"] == FIRST
    rolling = result["rolling"]
    assert (rolling["window"], rolling["step"]) == ("1Y", "monthly")
    assert len(rolling["volatility"]) == len(rolling["sharpe"]) > 0
    assert rolling["volatility"][-1]["frequency"] == "weekly"
    assert isinstance(rolling["volatility"][-1]["flags"], list)
    assert result["rolling_correlation"] is None


def test_get_ex_ante_risk_returns_a_requested_rolling_pair(fake_client):
    result = _get([("A", "3"), ("B", "1")], rolling_pair=("A", None))

    ExAnteRiskResponse.model_validate(result)
    pair = result["rolling_correlation"]
    assert {k: pair[k] for k in ("a", "b", "window", "step", "reason")} == {
        "a": "A",
        "b": "portfolio",
        "window": "52W",
        "step": "weekly",
        "reason": None,
    }
    assert pair["points"] and all(p["n_obs"] <= 52 for p in pair["points"])
    assert pair["points"][0]["frequency"] == "weekly"


def test_get_ex_ante_risk_with_nothing_included_has_only_statuses(fake_client):
    result = _get([("A", "1"), ("X", "1")], exclude={"A"}, rolling_pair=("X", None))

    ExAnteRiskResponse.model_validate(result)
    assert [s["status"] for s in result["statuses"]] == ["excluded", "not_tracked"]
    for key in (
        "window",
        "sigma_p",
        "diversification_ratio",
        "effective_n",
        "holdings",
        "constant_weight",
        "correlation",
        "rolling",
        "rolling_correlation",
    ):
        assert result[key] is None
    assert result["benchmark"]["available"] is False


def test_get_ex_ante_risk_logs_a_start_line_and_a_summary(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        _get([("A", "1"), ("X", "1")])

    assert "get-ex-ante-risk: measuring ex-ante risk of 2 asset(s)." in caplog.text
    assert "get-ex-ante-risk: done, 1 of 2 asset(s) included" in caplog.text


def test_get_ex_ante_risk_warns_when_no_asset_could_be_included(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        _get([("X", "1")])

    warning = next(r for r in caplog.records if "get-ex-ante-risk: done" in r.getMessage())
    assert warning.levelno == logging.WARNING
    assert "no asset could be included" in warning.getMessage()


def test_get_ex_ante_risk_needs_data_store_settings(monkeypatch):
    monkeypatch.setattr(risk_jobs, "load_settings", lambda: _settings(datastore_api_url=None))

    with pytest.raises(SettingsError, match="ex-ante risk"):
        _get([("A", "1")])
