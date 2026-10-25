"""HttpPortfolioClient's regime view (RK-28): `regimes` on GET /risk/ex-post and POST
/risk/ex-ante. The JSON is written out in the shape portfolio serves: each regime has its own
dates and the window it was measured on, with the portfolio's and the benchmark's cumulative
return and max drawdown as full risk figures."""

import copy
from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    RISK_FIGURE_KEYS,
    RISK_WINDOWS,
    RegimeFigures,
    RegimeRow,
    RegimeView,
    RiskFigure,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
END = "2026-10-16"


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


def _ok(body):
    return _client(lambda request: httpx.Response(200, json=body))


def _figure(value=0.1, start="2025-02-19", end="2025-04-08", **changes):
    return {
        "value": value,
        "window_start": start,
        "window_end": end,
        "frequency": "daily",
        "q": 252.0,
        "n_obs": 33,
        "n_gaps": 0,
        "flags": [],
        "reason": None,
        "standard_error": None,
        **changes,
    }


UNAVAILABLE = "benchmark unavailable: not tracked"


def _regimes():
    return {
        "available": True,
        "reason": None,
        "total_loaded": 94,
        "rows": [
            {
                "regime_id": "spx_corr_2025",
                "dimension": "drawdown",
                "scope": "SPX",
                "name": "2025 tariff selloff",
                "severity": "moderate",
                "source": "manual_approx",
                "start_date": "2025-02-19",
                "end_date": "2025-04-08",
                "measured_start": "2025-02-19",
                "measured_end": "2025-04-08",
                "partial": False,
                "ongoing": False,
                "portfolio": {
                    "cumulative_return": _figure(-0.11),
                    "max_drawdown": _figure(-0.14, flags=["short_regime"]),
                },
                "benchmark": {
                    "cumulative_return": _figure(-0.12),
                    "max_drawdown": _figure(-0.16, flags=["short_regime"]),
                },
            },
            {
                "regime_id": "ukraine_war",
                "dimension": "event",
                "scope": "GLOBAL",
                "name": "Russian invasion of Ukraine",
                "severity": None,
                "source": "manual",
                "start_date": "2022-02-24",
                "end_date": None,
                "measured_start": "2023-01-02",
                "measured_end": END,
                "partial": True,
                "ongoing": True,
                "portfolio": {
                    "cumulative_return": _figure(0.3, "2023-01-02", END, n_obs=950),
                    "max_drawdown": _figure(-0.12, "2023-01-02", END, n_obs=950),
                },
                "benchmark": {
                    "cumulative_return": _figure(None, "2023-01-02", END, reason=UNAVAILABLE),
                    "max_drawdown": _figure(None, "2023-01-02", END, reason=UNAVAILABLE),
                },
            },
        ],
    }


def _ex_post_body(**changes):
    window = {
        "start": "2023-10-16",
        "end": END,
        "covered": True,
        **{name: _figure() for name in RISK_FIGURE_KEYS},
        "peak_date": None,
        "trough_date": None,
        "recovery_date": None,
        "max_days_under_water": None,
        "relative": {
            name: _figure()
            for name in ("beta", "correlation", "tracking_error", "information_ratio")
        },
    }
    return {
        "label": "ex-post · invested assets",
        "base_date": "2023-01-02",
        "end_date": END,
        "rf_available": True,
        "benchmark": {
            "ticker": "VWCE.DE",
            "available": True,
            "reason": None,
            "first_date": "2019-07-29",
        },
        "windows": [{**window, "label": label} for label in RISK_WINDOWS],
        "n_excluded": 0,
        "excluded": [],
        "rolling": None,
        "regimes": _regimes(),
        **changes,
    }


def _ex_ante_body(**changes):
    """Nothing included but the regimes, the shortest valid ex-ante body."""
    return {
        "label": "ex-ante · constant weights",
        "currency": "eur",
        "rf_available": True,
        "benchmark": {
            "ticker": "VWCE.DE",
            "available": True,
            "reason": None,
            "first_date": "2019-07-29",
        },
        "statuses": [{"ticker": "IWDA.AS", "status": "included", "reason": None}],
        "window": None,
        "sigma_p": None,
        "diversification_ratio": None,
        "effective_n": None,
        "holdings": None,
        "constant_weight": None,
        "correlation": None,
        "fx_decomposition": None,
        "rolling": None,
        "rolling_correlation": None,
        "regimes": _regimes(),
        **changes,
    }


async def _ex_post(body):
    return await _ok(body).ex_post_risk(user_id="u", benchmark="VWCE.DE")


async def _ex_ante(body):
    return await _ok(body).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )


def _risk_figure(value, start, end, *, n_obs=33, flags=(), reason=None):
    return RiskFigure(value, start, end, "daily", 252.0, n_obs, 0, flags, reason, None)


async def test_ex_post_parses_each_regime_with_its_own_and_its_measured_dates():
    view = (await _ex_post(_ex_post_body())).regimes

    crash_start, crash_end = date(2025, 2, 19), date(2025, 4, 8)
    assert (view.available, view.reason, view.total_loaded) == (True, None, 94)
    crash, war = view.rows
    assert crash == RegimeRow(
        regime_id="spx_corr_2025",
        dimension="drawdown",
        scope="SPX",
        name="2025 tariff selloff",
        severity="moderate",
        source="manual_approx",
        start_date=crash_start,
        end_date=crash_end,
        measured_start=crash_start,
        measured_end=crash_end,
        partial=False,
        ongoing=False,
        portfolio=RegimeFigures(
            cumulative_return=_risk_figure(-0.11, crash_start, crash_end),
            max_drawdown=_risk_figure(-0.14, crash_start, crash_end, flags=("short_regime",)),
        ),
        benchmark=RegimeFigures(
            cumulative_return=_risk_figure(-0.12, crash_start, crash_end),
            max_drawdown=_risk_figure(-0.16, crash_start, crash_end, flags=("short_regime",)),
        ),
    )
    assert (war.end_date, war.measured_start, war.partial, war.ongoing) == (
        None,
        date(2023, 1, 2),
        True,
        True,
    )
    assert war.severity is None
    assert war.benchmark.max_drawdown.reason == UNAVAILABLE


async def test_ex_post_regimes_are_none_when_portfolio_has_nothing_to_measure():
    body = _ex_post_body(windows=[], base_date=None, end_date=None, regimes=None)

    assert (await _ex_post(body)).regimes is None


@pytest.mark.parametrize(
    "regimes",
    [
        {"available": False, "reason": "no regimes loaded", "total_loaded": 0, "rows": []},
        {
            "available": False,
            "reason": "regimes unavailable: data-store call failed",
            "total_loaded": None,
            "rows": [],
        },
        {"available": True, "reason": None, "total_loaded": 94, "rows": []},
    ],
)
async def test_a_view_without_rows_keeps_its_reason(regimes):
    view = (await _ex_post(_ex_post_body(regimes=regimes))).regimes

    assert view == RegimeView(regimes["available"], regimes["reason"], regimes["total_loaded"], ())


async def test_ex_ante_parses_the_regimes_of_the_mix():
    view = (await _ex_ante(_ex_ante_body())).regimes

    assert [row.regime_id for row in view.rows] == ["spx_corr_2025", "ukraine_war"]


async def test_ex_ante_regimes_are_none_when_nothing_was_included():
    assert (await _ex_ante(_ex_ante_body(regimes=None))).regimes is None


@pytest.mark.parametrize("call", [_ex_post, _ex_ante])
async def test_a_body_without_regimes_is_malformed(call):
    body = _ex_post_body() if call is _ex_post else _ex_ante_body()
    del body["regimes"]

    with pytest.raises(PortfolioUnavailable):
        await call(body)


def _changed(change):
    body = _ex_post_body()
    change(body["regimes"])
    return body


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda view: view.pop("total_loaded"), id="no total"),
        pytest.param(lambda view: view.update(available="true"), id="available not a bool"),
        pytest.param(lambda view: view["rows"][0].pop("measured_start"), id="row key missing"),
        pytest.param(lambda view: view["rows"][0].update(partial=1), id="partial not a bool"),
        pytest.param(lambda view: view["rows"][0].update(end_date="soon"), id="bad date"),
        pytest.param(
            lambda view: view["rows"][0]["portfolio"].update(max_drawdown=None),
            id="null figure",
        ),
        pytest.param(
            lambda view: view["rows"][0]["benchmark"].pop("cumulative_return"),
            id="figure missing",
        ),
        pytest.param(lambda view: view.update(total_loaded="94"), id="total not an int"),
    ],
)
async def test_a_malformed_regime_view_is_unavailable(change):
    with pytest.raises(PortfolioUnavailable):
        await _ex_post(_changed(change))


async def test_the_regimes_dont_change_the_body_they_came_from():
    body = _ex_post_body()
    before = copy.deepcopy(body)

    await _ex_post(body)

    assert body == before
