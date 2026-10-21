"""GET /risk/ex-post (RK-11): argument coercion and the response model. jobs.get_ex_post_risk is
stubbed; it's tested in tests/unit/test_jobs_ex_post_risk.py."""

import uuid

from portfolio import jobs


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

    monkeypatch.setattr(jobs, "get_ex_post_risk", fake_get_ex_post_risk)
    user_id = uuid.uuid4()

    response = client.get("/risk/ex-post", params={"user_id": str(user_id)})

    assert response.status_code == 200
    assert response.json() == _EX_POST
    assert captured["user_id"] == user_id


def test_ex_post_with_nothing_to_measure_has_no_windows(monkeypatch, client):
    empty = {
        "label": "ex-post · invested assets",
        "base_date": None,
        "end_date": None,
        "rf_available": False,
        "windows": [],
        "n_excluded": 0,
        "excluded": [],
    }
    monkeypatch.setattr(jobs, "get_ex_post_risk", lambda **kw: empty)

    response = client.get("/risk/ex-post", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json() == empty


def test_ex_post_requires_a_user(client):
    response = client.get("/risk/ex-post")

    assert response.status_code == 422
