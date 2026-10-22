"""GET /risk/ex-post (RK-11), POST /risk/ex-ante (RK-17) and POST /risk/what-if (RK-19):
argument coercion, request validation and the response models. The jobs are stubbed; they're
tested in tests/unit/test_jobs_ex_post_risk.py, test_jobs_ex_ante_risk.py and
test_jobs_what_if.py."""

import uuid
from decimal import Decimal

import pytest
from portfolio import analytics_jobs
from portfolio.risk.what_if import PRO_RATA, Funding
from portfolio.risk.windows import StandardWindow


def _figure(value, frequency="daily", flags=(), reason=None, standard_error=None):
    return {
        "value": value,
        "window_start": "2025-01-09",
        "window_end": "2026-01-09",
        "frequency": frequency,
        "q": 52.0 if frequency == "weekly" else 251.3,
        "n_obs": 260,
        "n_gaps": 0,
        "flags": list(flags),
        "reason": reason,
        "standard_error": standard_error,
    }


_NAMES = (
    "cumulative_return",
    "cagr",
    "volatility",
    "downside_deviation",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "var_95",
    "cvar_95",
    "var_99",
    "cvar_99",
)

_EX_POST = {
    "label": "ex-post · invested assets",
    "base_date": "2024-01-05",
    "end_date": "2026-01-09",
    "rf_available": True,
    "benchmark": {
        "ticker": "VWCE.DE",
        "available": True,
        "reason": None,
        "first_date": "2019-07-29",
    },
    "windows": [
        {
            "label": "1Y",
            "start": "2025-01-09",
            "end": "2026-01-09",
            "covered": True,
            "peak_date": "2025-03-03",
            "trough_date": "2025-04-08",
            "recovery_date": None,
            "max_days_under_water": 312,
            **{name: _figure(0.1, flags=["nonsync_daily"]) for name in _NAMES},
            "sharpe": _figure(0.5, "weekly", standard_error=0.9),
            "var_99": _figure(None, reason="insufficient history (have 260, need 500)"),
            "relative": {
                "beta": _figure(None, "weekly", reason="insufficient history (have 52, need 104)"),
                "correlation": _figure(
                    None, "weekly", reason="insufficient history (have 52, need 104)"
                ),
                "tracking_error": _figure(0.03, "weekly"),
                "information_ratio": _figure(-0.4, "weekly"),
            },
        }
    ],
    "n_excluded": 1,
    "excluded": [{"ticker": "U", "status": "no_fx_rate", "reason": "no FX series for HKD"}],
}


def test_ex_post_passes_the_user_through_and_returns_the_jobs_own_dict(monkeypatch, client):
    captured = {}

    def fake_get_ex_post_risk(**kw):
        captured.update(kw)
        return _EX_POST

    monkeypatch.setattr(analytics_jobs, "get_ex_post_risk", fake_get_ex_post_risk)
    user_id = uuid.uuid4()

    response = client.get("/risk/ex-post", params={"user_id": str(user_id)})

    assert response.status_code == 200
    assert response.json() == _EX_POST
    assert captured["user_id"] == user_id
    assert captured["benchmark"] == "VWCE.DE"  # the default (RK-13)


def test_ex_post_passes_a_named_benchmark_through(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(
        analytics_jobs, "get_ex_post_risk", lambda **kw: captured.update(kw) or _EX_POST
    )

    response = client.get(
        "/risk/ex-post", params={"user_id": str(uuid.uuid4()), "benchmark": "VWRL.AS"}
    )

    assert response.status_code == 200
    assert captured["benchmark"] == "VWRL.AS"


def test_ex_post_rejects_an_empty_or_too_long_benchmark(monkeypatch, client):
    monkeypatch.setattr(
        analytics_jobs, "get_ex_post_risk", lambda **kw: pytest.fail("job must not run")
    )

    for benchmark in ("", "B" * 21):
        response = client.get(
            "/risk/ex-post", params={"user_id": str(uuid.uuid4()), "benchmark": benchmark}
        )
        assert response.status_code == 422


def test_ex_post_with_nothing_to_measure_has_no_windows(monkeypatch, client):
    empty = {
        "label": "ex-post · invested assets",
        "base_date": None,
        "end_date": None,
        "rf_available": False,
        "benchmark": {
            "ticker": "VWCE.DE",
            "available": False,
            "reason": "not fetched: nothing to measure",
            "first_date": None,
        },
        "windows": [],
        "n_excluded": 0,
        "excluded": [],
    }
    monkeypatch.setattr(analytics_jobs, "get_ex_post_risk", lambda **kw: empty)

    response = client.get("/risk/ex-post", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json() == empty


def test_ex_post_requires_a_user(client):
    response = client.get("/risk/ex-post")

    assert response.status_code == 422


# --- POST /risk/ex-ante (RK-17) ---

_EX_ANTE = {
    "label": "ex-ante · constant weights",
    "currency": "eur",
    "rf_available": True,
    "window": {
        "label": "3Y",
        "requested_start": "2023-01-09",
        "start": "2024-01-05",
        "end": "2026-01-09",
        "limited_by": ["B"],
    },
    "benchmark": {
        "ticker": "VWCE.DE",
        "available": True,
        "reason": None,
        "first_date": "2019-07-29",
    },
    "statuses": [
        {"ticker": "A", "status": "included", "reason": None},
        {"ticker": "B", "status": "included", "reason": None},
        {"ticker": "C", "status": "excluded", "reason": "excluded by request"},
    ],
    "sigma_p": _figure(0.14, "weekly"),
    "diversification_ratio": _figure(1.16, "weekly"),
    "effective_n": 1.92,
    "holdings": [
        {
            "ticker": "A",
            "weight": 0.6,
            "volatility": 0.2,
            "mcr": 0.19,
            "cr": 0.12,
            "pcr": 0.84,
            "corr_with_portfolio": 0.96,
        },
        {
            "ticker": "B",
            "weight": 0.4,
            "volatility": None,
            "mcr": None,
            "cr": None,
            "pcr": None,
            "corr_with_portfolio": None,
        },
    ],
    "constant_weight": {
        "peak_date": "2025-03-03",
        "trough_date": "2025-04-08",
        "recovery_date": None,
        "max_days_under_water": 312,
        **{name: _figure(0.1) for name in _NAMES},
    },
    "correlation": {
        "keys": ["A", "VWCE.DE"],
        "benchmark_key": "VWCE.DE",
        "cells": [
            {"a": "A", "b": "A", "figure": _figure(1.0, "weekly")},
            {"a": "A", "b": "VWCE.DE", "figure": _figure(0.8, "weekly")},
            {"a": "VWCE.DE", "b": "VWCE.DE", "figure": _figure(1.0, "weekly")},
        ],
    },
}


def _ex_ante_body(**kw):
    return {
        "assets": [
            {"ticker": "A", "weight": "60"},
            {"ticker": "B", "weight": "40"},
            {"ticker": "C", "weight": "5"},
        ],
        **kw,
    }


def test_ex_ante_parses_the_mix_with_its_defaults_and_returns_the_jobs_own_dict(
    monkeypatch, client
):
    captured = {}
    monkeypatch.setattr(
        analytics_jobs, "get_ex_ante_risk", lambda **kw: captured.update(kw) or _EX_ANTE
    )

    response = client.post("/risk/ex-ante", json=_ex_ante_body())

    assert response.status_code == 200
    assert response.json() == _EX_ANTE
    assert captured["assets"] == [("A", Decimal("60")), ("B", Decimal("40")), ("C", Decimal("5"))]
    assert captured["window"] is StandardWindow.Y3
    assert captured["benchmark"] == "VWCE.DE"
    assert captured["currency"] == "eur"
    assert captured["exclude"] == []


def test_ex_ante_passes_its_options_through(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(
        analytics_jobs, "get_ex_ante_risk", lambda **kw: captured.update(kw) or _EX_ANTE
    )

    response = client.post(
        "/risk/ex-ante",
        json=_ex_ante_body(window="Max", benchmark="VWRL.AS", currency="local", exclude=["C"]),
    )

    assert response.status_code == 200
    assert captured["window"] is StandardWindow.MAX
    assert captured["benchmark"] == "VWRL.AS"
    assert captured["currency"] == "local"
    assert captured["exclude"] == ["C"]


@pytest.mark.parametrize(
    "body",
    [
        {"assets": []},
        {"assets": [{"ticker": "A", "weight": "1"}, {"ticker": "A", "weight": "2"}]},
        {"assets": [{"ticker": "A", "weight": "0"}]},
        _ex_ante_body(exclude=["Z"]),
        _ex_ante_body(exclude=["A", "B", "C"]),
        _ex_ante_body(window="2Y"),
        _ex_ante_body(currency="usd"),
        _ex_ante_body(benchmark=""),
    ],
    ids=[
        "no assets",
        "duplicate ticker",
        "zero weight",
        "exclude not in the mix",
        "exclude everything",
        "unknown window",
        "unknown currency",
        "empty benchmark",
    ],
)
def test_ex_ante_rejects_an_invalid_request(monkeypatch, client, body):
    monkeypatch.setattr(
        analytics_jobs, "get_ex_ante_risk", lambda **kw: pytest.fail("job must not run")
    )

    assert client.post("/risk/ex-ante", json=body).status_code == 422


# --- POST /risk/what-if (RK-19) ------------------------------------------------------------------


def _row(before, after, delta):
    return {
        "before": None if before is None else _figure(before, "weekly"),
        "after": _figure(after, "weekly"),
        "delta": delta,
    }


_WHAT_IF = {
    "label": "ex-ante · constant weights",
    "rf_available": True,
    "statuses": [
        {"ticker": "A", "status": "included", "reason": None},
        {"ticker": "B", "status": "included", "reason": None},
    ],
    "candidate": {
        "ticker": "C",
        "status": "included",
        "reason": None,
        "requested_weight": 0.05,
        "weight": 0.05,
        "capped": False,
        "survivorship": False,
    },
    "funding": {"rule": "pro_rata", "ticker": None, "reason": None},
    "window": {
        "label": "3Y",
        "requested_start": "2023-01-09",
        "start": "2023-01-13",
        "end": "2026-01-09",
        "limited_by": [],
        "shortened": False,
        "start_without_candidate": "2023-01-13",
        "end_without_candidate": "2026-01-09",
    },
    "weights": {"before": {"A": 0.6, "B": 0.4}, "after": {"A": 0.57, "B": 0.38, "C": 0.05}},
    "rows": {
        "sigma_p": _row(0.14, 0.135, -0.005),
        "diversification_ratio": _row(1.16, 1.2, 0.04),
        "candidate_pcr": _row(None, 0.03, None),
        "candidate_correlation": _row(None, 0.4, None),
        "sharpe": {**_row(0.8, 0.82, 0.02), "delta_se": 0.03},
        "max_drawdown": {
            "before": _figure(-0.2),
            "after": _figure(-0.19),
            "delta": 0.01,
        },
    },
    "threshold": {
        "applicable": True,
        "reason": None,
        "candidate_sharpe": _figure(0.5, "weekly", standard_error=0.58),
        "correlation": 0.4,
        "portfolio_sharpe": 0.8,
        "threshold": 0.32,
        "improves": True,
    },
}


def _what_if_body(**kw):
    return {
        "assets": [{"ticker": "A", "weight": "60"}, {"ticker": "B", "weight": "40"}],
        "candidate": {"ticker": "C", "weight": "0.05"},
        **kw,
    }


def test_what_if_parses_the_request_with_its_defaults_and_returns_the_jobs_own_dict(
    monkeypatch, client
):
    captured = {}
    monkeypatch.setattr(analytics_jobs, "get_what_if", lambda **kw: captured.update(kw) or _WHAT_IF)

    response = client.post("/risk/what-if", json=_what_if_body())

    assert response.status_code == 200
    assert response.json() == _WHAT_IF
    assert captured["assets"] == [("A", Decimal("60")), ("B", Decimal("40"))]
    assert (captured["candidate"], captured["weight"]) == ("C", Decimal("0.05"))
    assert captured["funding"] == PRO_RATA
    assert captured["window"] is StandardWindow.Y3
    assert captured["exclude"] == []


def test_what_if_passes_replace_funding_and_its_options_through(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(analytics_jobs, "get_what_if", lambda **kw: captured.update(kw) or _WHAT_IF)
    body = _what_if_body(
        assets=[
            {"ticker": "A", "weight": "60"},
            {"ticker": "B", "weight": "40"},
            {"ticker": "D", "weight": "5"},
        ],
        funding="replace:B",
        window="5Y",
        exclude=["D"],
    )

    assert client.post("/risk/what-if", json=body).status_code == 200
    assert captured["funding"] == Funding.replace("B")
    assert captured["window"] is StandardWindow.Y5
    assert captured["exclude"] == ["D"]


def test_what_if_with_no_report_validates_with_null_sections(monkeypatch, client):
    nothing = {
        **_WHAT_IF,
        "rf_available": False,
        "candidate": {
            **_WHAT_IF["candidate"],
            "status": "not_tracked",
            "reason": "not tracked",
            "weight": None,
            "capped": None,
        },
        **dict.fromkeys(("window", "weights", "rows", "threshold")),
    }
    monkeypatch.setattr(analytics_jobs, "get_what_if", lambda **kw: nothing)

    response = client.post("/risk/what-if", json=_what_if_body())

    assert response.status_code == 200
    assert response.json() == nothing


@pytest.mark.parametrize(
    "body",
    [
        _what_if_body(candidate={"ticker": "A", "weight": "0.05"}),
        _what_if_body(candidate={"ticker": "C", "weight": "0"}),
        _what_if_body(candidate={"ticker": "C", "weight": "1"}),
        _what_if_body(candidate={"ticker": "C", "weight": "5"}),
        _what_if_body(candidate={"ticker": "", "weight": "0.05"}),
        {"assets": [{"ticker": "A", "weight": "1"}]},
        _what_if_body(funding="cash"),
        _what_if_body(funding="replace:"),
        _what_if_body(funding="replace:Z"),
        _what_if_body(funding="replace:B", exclude=["B"]),
        _what_if_body(exclude=["Z"]),
        _what_if_body(exclude=["A", "B"]),
        _what_if_body(assets=[]),
        _what_if_body(window="2Y"),
    ],
    ids=[
        "candidate already held",
        "zero weight",
        "whole weight",
        "a percentage, not a fraction",
        "empty candidate",
        "no candidate",
        "unknown funding",
        "replace without a ticker",
        "replace a ticker not in the mix",
        "replace an excluded holding",
        "exclude not in the mix",
        "exclude everything",
        "no assets",
        "unknown window",
    ],
)
def test_what_if_rejects_an_invalid_request(monkeypatch, client, body):
    monkeypatch.setattr(analytics_jobs, "get_what_if", lambda **kw: pytest.fail("job must not run"))

    assert client.post("/risk/what-if", json=body).status_code == 422
