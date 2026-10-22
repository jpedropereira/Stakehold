"""analytics_jobs.get_what_if (RK-19): builds the data-store client, runs
risk/flows.compute_what_if, logs its start and summary, and returns plain builtins the API's
WhatIfResponse accepts. The flow is tested in tests/unit/risk/test_flows_what_if.py and the
figures in test_what_if.py; this runs it for real on a fake client to check the shape. Small
helpers are repeated per file (no shared test modules).

Calendar: 2020-01-03 is a Friday. A, B and C are EUR, with three years of weekday returns from a
fixed seed. A and B are tracked; S is priced only through a fund's holdings. Anything else is
untracked.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from portfolio import analytics_jobs
from portfolio.api.models import WhatIfResponse
from portfolio.datastore_client.base import (
    PricePoint,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
from portfolio.risk.what_if import PRO_RATA, Funding
from portfolio.risk.windows import StandardWindow
from portfolio.settings import SettingsError

FIRST = date(2020, 1, 3)
END = date(2023, 3, 1)
_SEEDS = {"A": 1, "B": 2, "C": 3, "S": 4}


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
            status = None if ticker == "S" else "ready"
            return TickerInfo(
                exists=True, has_returns=True, status=status, currency="EUR", fx_currency="EUR"
            )
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
    monkeypatch.setattr(analytics_jobs, "load_settings", _settings)
    monkeypatch.setattr(
        analytics_jobs, "require_datastore_client", lambda settings, command: client
    )
    return client


def _get(assets=(("A", "3"), ("B", "1")), candidate="C", weight="0.2", funding=PRO_RATA, **kw):
    kw.setdefault("window", StandardWindow.Y3)
    return analytics_jobs.get_what_if(
        assets=[(t, Decimal(w)) for t, w in assets],
        candidate=candidate,
        weight=Decimal(weight),
        funding=funding,
        **kw,
    )


def test_get_what_if_returns_builtins_the_response_model_accepts(fake_client):
    result = _get()

    WhatIfResponse.model_validate(result)
    assert result["label"] == "ex-ante · constant weights"
    assert result["rf_available"] is True
    assert [s["status"] for s in result["statuses"]] == ["included", "included"]
    assert result["candidate"] == {
        "ticker": "C",
        "status": "included",
        "reason": None,
        "requested_weight": pytest.approx(0.2),
        "weight": pytest.approx(0.2),
        "capped": False,
        "survivorship": False,
    }
    assert result["funding"] == {"rule": "pro_rata", "ticker": None, "reason": None}
    window = result["window"]
    assert window["label"] == "3Y"
    assert window["shortened"] is False
    assert window["start_without_candidate"] == window["start"]
    assert result["weights"]["before"] == pytest.approx({"A": 0.75, "B": 0.25})
    assert result["weights"]["after"] == pytest.approx({"A": 0.6, "B": 0.2, "C": 0.2})

    rows = result["rows"]
    assert list(rows) == [
        "sigma_p",
        "diversification_ratio",
        "candidate_pcr",
        "candidate_correlation",
        "sharpe",
        "max_drawdown",
    ]
    sigma_p = rows["sigma_p"]
    assert sigma_p["delta"] == pytest.approx(sigma_p["after"]["value"] - sigma_p["before"]["value"])
    assert sigma_p["after"]["frequency"] == "weekly"
    assert rows["candidate_pcr"]["before"] is None
    assert rows["sharpe"]["delta_se"] > 0
    assert rows["max_drawdown"]["after"]["frequency"] == "daily"
    threshold = result["threshold"]
    assert threshold["applicable"] is True
    assert threshold["threshold"] == pytest.approx(
        threshold["correlation"] * threshold["portfolio_sharpe"]
    )


def test_get_what_if_under_replace_reports_the_cap_and_no_threshold(fake_client):
    result = _get(weight="0.4", funding=Funding.replace("B"))

    WhatIfResponse.model_validate(result)
    assert result["funding"] == {"rule": "replace", "ticker": "B", "reason": None}
    assert result["candidate"]["requested_weight"] == pytest.approx(0.4)
    assert result["candidate"]["weight"] == pytest.approx(0.25)
    assert result["candidate"]["capped"] is True
    assert result["weights"]["after"] == pytest.approx({"A": 0.75, "B": 0.0, "C": 0.25})
    threshold = result["threshold"]
    assert (threshold["applicable"], threshold["reason"]) == (
        False,
        "shown under pro-rata funding only",
    )
    assert threshold["candidate_sharpe"] is None


def test_get_what_if_flags_a_constituent_only_candidate(fake_client):
    result = _get(candidate="S")

    WhatIfResponse.model_validate(result)
    assert result["candidate"]["survivorship"] is True
    assert "survivorship" in result["rows"]["sigma_p"]["after"]["flags"]
    assert "survivorship" not in result["rows"]["sigma_p"]["before"]["flags"]
    assert "survivorship" in result["threshold"]["candidate_sharpe"]["flags"]


def test_get_what_if_with_an_unmeasurable_candidate_has_only_statuses(fake_client):
    result = _get(candidate="X")

    WhatIfResponse.model_validate(result)
    assert (result["candidate"]["status"], result["candidate"]["reason"]) == (
        "not_tracked",
        "not tracked",
    )
    assert (result["candidate"]["weight"], result["candidate"]["capped"]) == (None, None)
    for key in ("window", "weights", "rows", "threshold"):
        assert result[key] is None


def test_get_what_if_with_an_unmeasurable_replaced_holding_says_why(fake_client):
    result = _get(assets=[("A", "1"), ("X", "1")], funding=Funding.replace("X"))

    WhatIfResponse.model_validate(result)
    assert result["funding"]["reason"] == (
        "X, the holding to replace, can't be measured: not tracked"
    )
    assert result["rows"] is None


def test_get_what_if_logs_a_start_line_and_a_summary(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.analytics_jobs"):
        _get()

    assert "get-what-if: comparing 2 asset(s) before and after adding C." in caplog.text
    assert "get-what-if: done, 2 of 2 asset(s) included, window 3Y, risk-free rate available" in (
        caplog.text
    )


def test_get_what_if_warns_when_there_is_nothing_to_compare(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.analytics_jobs"):
        _get(candidate="X")

    warning = next(r for r in caplog.records if "get-what-if: done" in r.getMessage())
    assert warning.levelno == logging.WARNING
    assert "nothing to compare: the candidate X: not tracked" in warning.getMessage()


def test_get_what_if_needs_data_store_settings(monkeypatch):
    monkeypatch.setattr(analytics_jobs, "load_settings", lambda: _settings(datastore_api_url=None))

    with pytest.raises(SettingsError, match="a what-if"):
        _get()
