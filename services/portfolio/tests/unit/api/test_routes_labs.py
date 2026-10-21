"""POST /labs/backtest: request validation, and the job's result passed through. Most tests stub
`jobs.get_backtest` outright, like the other route tests; the last one stubs only the data-store
client, so the job, the flow and the calculator run for real and the JSON shape is checked whole.
"""

from datetime import date
from decimal import Decimal

import pytest
from portfolio import jobs
from portfolio.datastore_client.base import (
    FxPoint,
    PricePoint,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
from portfolio.labs.calculator import Period


def _body(assets=None, period="1Y"):
    if assets is None:
        assets = [{"ticker": "IWDA.AS", "weight": "60"}, {"ticker": "AAPL", "weight": "40"}]
    return {"assets": assets, "period": period}


def _empty_result(statuses):
    return {
        "end_date": None,
        "mix": None,
        "assets": [],
        "growth": None,
        "statuses": statuses,
        "risk": None,
    }


def test_backtest_parses_the_mix_and_returns_the_jobs_result(monkeypatch, client):
    calls = []
    result = _empty_result(
        [
            {"ticker": "IWDA.AS", "status": "failed", "reason": "couldn't reach data-store"},
            {"ticker": "AAPL", "status": "not_tracked", "reason": "not tracked"},
        ]
    )

    def fake_get_backtest(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(jobs, "get_backtest", fake_get_backtest)

    response = client.post("/labs/backtest", json=_body())

    assert response.status_code == 200
    assert response.json() == result
    assert calls[0]["assets"] == [("IWDA.AS", Decimal("60")), ("AAPL", Decimal("40"))]
    assert calls[0]["period"] is Period.Y1


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_body(assets=[]), id="empty mix"),
        pytest.param(
            _body(assets=[{"ticker": f"T{i}", "weight": "1"} for i in range(51)]),
            id="51 assets",
        ),
        pytest.param(
            _body(assets=[{"ticker": "A", "weight": "1"}, {"ticker": "A", "weight": "2"}]),
            id="duplicate ticker",
        ),
        pytest.param(_body(assets=[{"ticker": "A", "weight": "0"}]), id="zero weight"),
        pytest.param(_body(assets=[{"ticker": "A", "weight": "-1"}]), id="negative weight"),
        pytest.param(_body(assets=[{"ticker": "A", "weight": "NaN"}]), id="NaN weight"),
        pytest.param(_body(assets=[{"ticker": "A", "weight": "Infinity"}]), id="infinite weight"),
        pytest.param(_body(assets=[{"ticker": "", "weight": "1"}]), id="empty ticker"),
        pytest.param(_body(assets=[{"ticker": "A" * 21, "weight": "1"}]), id="21-char ticker"),
        pytest.param(_body(period="10Y"), id="unknown period"),
        pytest.param({"assets": [{"ticker": "A", "weight": "1"}]}, id="missing period"),
    ],
)
def test_backtest_rejects_an_invalid_mix_with_422(monkeypatch, client, body):
    monkeypatch.setattr(jobs, "get_backtest", lambda **kw: pytest.fail("job must not run"))

    response = client.post("/labs/backtest", json=body)

    assert response.status_code == 422


def test_backtest_accepts_exactly_fifty_assets(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_backtest", lambda **kw: _empty_result([]))

    response = client.post(
        "/labs/backtest",
        json=_body(assets=[{"ticker": f"T{i}", "weight": "1"} for i in range(50)]),
    )

    assert response.status_code == 200


class _FakeDataStoreClient:
    def get_ticker_info(self, ticker):
        return {
            "E": TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR"),
            "U": TickerInfo(exists=True, has_returns=True, currency="USD", fx_currency="USD"),
        }[ticker]

    def get_price_series(self, ticker, start, end):
        returns = {"E": "0.10", "U": "0"}[ticker]
        return [
            PricePoint(date(2026, 1, 5), Decimal("100"), None),
            PricePoint(date(2026, 1, 6), Decimal("100"), Decimal(returns)),
        ]

    def get_fx_rate_series(self, base, quote, start, end):
        return [FxPoint(date(2026, 1, 5), Decimal("1.25")), FxPoint(date(2026, 1, 6), Decimal("1"))]

    def get_risk_free_series(self, start, end):
        point = RiskFreePoint(date(2026, 1, 2), Decimal("1.9300"), "estr")
        return RiskFreeSeries(first_date=date(1999, 1, 4), points=[point])


def test_backtest_serializes_a_real_result_end_to_end(monkeypatch, client):
    monkeypatch.setattr(
        jobs, "_require_datastore_client", lambda settings, command: _FakeDataStoreClient()
    )

    response = client.post(
        "/labs/backtest",
        json=_body(
            assets=[{"ticker": "E", "weight": "1"}, {"ticker": "U", "weight": "3"}], period="Max"
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["end_date"] == "2026-01-06"
    assert list(body["mix"]["periods"]) == ["1M", "3M", "6M", "1Y", "2Y", "5Y", "Max"]
    # E: +10%. U: flat in USD, but USD went from 1.25 to 1 per EUR, so +25% in EUR.
    # Mix: 0.25 x 1.10 + 0.75 x 1.25 - 1 = 0.2125.
    assert body["mix"]["periods"]["Max"] == {
        "start": "2026-01-05",
        "end": "2026-01-06",
        "covered": True,
        "return_pct": "0.21250000",
        "gaps": 0,
        "left_out": [],
    }
    # Two days of history cover no month period, so the mix leaves both out of 1M.
    assert body["mix"]["periods"]["1M"]["covered"] is False
    assert body["mix"]["periods"]["1M"]["return_pct"] is None
    assert body["mix"]["periods"]["1M"]["left_out"] == ["E", "U"]
    assert [(row["ticker"], row["weight"]) for row in body["assets"]] == [
        ("E", "0.25"),
        ("U", "0.75"),
    ]
    assert body["assets"][1]["periods"]["Max"]["return_pct"] == "0.25000000"
    assert body["growth"] == {
        "period": "Max",
        "dates": ["2026-01-05", "2026-01-06"],
        "mix": ["0.00000000", "0.21250000"],
        "assets": {"E": ["0.00000000", "0.10000000"], "U": ["0.00000000", "0.25000000"]},
        "left_out": [],
    }
    assert body["statuses"] == [
        {"ticker": "E", "status": "included", "reason": None},
        {"ticker": "U", "status": "included", "reason": None},
    ]


def test_backtest_writes_small_figures_in_fixed_point_not_scientific_notation(monkeypatch, client):
    result = _empty_result([])
    result["growth"] = {
        "period": "1M",
        "dates": [date(2026, 1, 5)],
        "mix": [Decimal("1.2E-7")],
        "assets": {},
    }
    monkeypatch.setattr(jobs, "get_backtest", lambda **kw: result)

    response = client.post("/labs/backtest", json=_body())

    assert response.json()["growth"]["mix"] == ["0.00000012"]


def test_backtest_serializes_left_out_tickers_and_null_points_in_a_line(monkeypatch, client):
    period = {
        "start": date(2026, 1, 5),
        "end": date(2026, 1, 6),
        "covered": True,
        "return_pct": Decimal("0.1"),
        "gaps": 0,
        "left_out": ["NEW"],
    }
    result = _empty_result([])
    result["end_date"] = date(2026, 1, 6)
    result["mix"] = {"ticker": None, "weight": Decimal("1"), "periods": {p: period for p in Period}}
    result["growth"] = {
        "period": "Max",
        "dates": [date(2026, 1, 5), date(2026, 1, 6)],
        "mix": [Decimal("0"), Decimal("0.1")],
        "assets": {"LATE": [None, Decimal("0")]},
        "left_out": ["NEW"],
    }
    monkeypatch.setattr(jobs, "get_backtest", lambda **kw: result)

    body = client.post("/labs/backtest", json=_body()).json()

    assert body["mix"]["periods"]["3M"]["left_out"] == ["NEW"]
    assert body["growth"]["assets"] == {"LATE": [None, "0"]}
    assert body["growth"]["left_out"] == ["NEW"]


def test_backtest_serializes_the_risk_section_end_to_end(monkeypatch, client):
    monkeypatch.setattr(
        jobs, "_require_datastore_client", lambda settings, command: _FakeDataStoreClient()
    )

    body = client.post(
        "/labs/backtest",
        json=_body(
            assets=[{"ticker": "E", "weight": "1"}, {"ticker": "U", "weight": "3"}], period="Max"
        ),
    ).json()

    risk = body["risk"]
    assert risk["label"] == "buy & hold (drifting)"
    assert (risk["end_date"], risk["common_start"]) == ("2026-01-06", "2026-01-05")
    assert risk["limited_by"] == ["E", "U"]
    assert risk["risk_free"] == {"available": True, "reason": None, "first_date": "1999-01-04"}
    assert list(risk["mix"]["windows"]) == ["1Y", "3Y", "5Y", "10Y", "Max"]
    assert [row["ticker"] for row in risk["assets"]] == ["E", "U"]
    assert risk["mix"]["windows"]["1Y"] == {
        "start": "2025-01-06",
        "end": "2026-01-06",
        "covered": False,
        "reason": "limited by E, U (history from 2026-01-05)",
        **{name: None for name in _WINDOW_FIGURES},
        "peak_date": None,
        "trough_date": None,
        "recovery_date": None,
        "max_days_under_water": None,
    }
    max_window = risk["mix"]["windows"]["Max"]
    # One day, so the cumulative return is the backtest's own: 0.25 x 1.10 + 0.75 x 1.25 - 1.
    cumulative = max_window["cumulative_return"]
    assert cumulative["value"] == pytest.approx(0.2125)
    assert {k: v for k, v in cumulative.items() if k != "value"} == {
        "window_start": "2026-01-05",
        "window_end": "2026-01-06",
        "frequency": "daily",
        "q": 365.25,
        "n_obs": 1,
        "n_gaps": 0,
        "flags": [],
        "reason": None,
        "standard_error": None,
    }
    # Greyed, with every flag listed in a stable order.
    assert max_window["var_95"]["value"] is None
    assert max_window["var_95"]["flags"] == ["insufficient_history", "nonsync_daily"]
    assert max_window["var_95"]["reason"] == "insufficient history (have 1, need 250)"
    assert risk["drawdown"] == {
        "period": "Max",
        "dates": ["2026-01-05", "2026-01-06"],
        "values": [0.0, 0.0],
    }


_WINDOW_FIGURES = (
    "cumulative_return",
    "cagr",
    "volatility",
    "downside_deviation",
    "sharpe",
    "sortino",
    "calmar",
    "max_drawdown",
    "var_95",
    "cvar_95",
    "var_99",
    "cvar_99",
)


def test_backtest_has_no_risk_section_when_no_asset_is_included(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_backtest", lambda **kw: _empty_result([]))

    body = client.post("/labs/backtest", json=_body()).json()

    assert body["risk"] is None
