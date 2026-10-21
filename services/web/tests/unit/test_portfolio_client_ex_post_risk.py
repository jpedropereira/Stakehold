"""HttpPortfolioClient.ex_post_risk: portfolio's GET /risk/ex-post (RK-11) -- the request it
sends, how the windows and their figures are parsed, and which failures become which error. The
JSON is written out in the shape portfolio's ExPostRiskResponse serves: figures are JSON numbers
and fractions (0.10 is 10%), VaR and CVaR positive losses, a max drawdown negative."""

import copy
from datetime import date

import httpx
import pytest
from web.portfolio_client.base import (
    RISK_FIGURES,
    RISK_WINDOWS,
    ExcludedHolding,
    ExPostRisk,
    ExPostWindow,
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


def _figure(value=0.1, **changes):
    return {
        "value": value,
        "window_start": "2025-10-16",
        "window_end": END,
        "frequency": "daily",
        "q": 252.0,
        "n_obs": 251,
        "n_gaps": 0,
        "flags": [],
        "reason": None,
        "standard_error": None,
        **changes,
    }


def _window(label, start="2025-10-16", **changes):
    window = {
        "label": label,
        "start": start,
        "end": END,
        "covered": True,
        **{name: _figure() for name in RISK_FIGURES},
        "peak_date": "2026-02-02",
        "trough_date": "2026-04-07",
        "recovery_date": None,
        "max_days_under_water": 256,
    }
    window.update(changes)
    return window


def _body(**changes):
    return {
        "label": "ex-post · invested assets",
        "base_date": "2023-01-02",
        "end_date": END,
        "rf_available": True,
        "windows": [_window(label) for label in RISK_WINDOWS],
        "n_excluded": 1,
        "excluded": [
            {"ticker": "7203.T", "status": "no_fx_rate", "reason": "no FX series for JPY"}
        ],
        **changes,
    }


async def test_ex_post_risk_gets_the_users_figures_with_the_secret():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["user_id"] = request.url.params.get("user_id")
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json=_body())

    await _client(handler).ex_post_risk(user_id="u-1")

    assert captured == {
        "method": "GET",
        "path": "/risk/ex-post",
        "user_id": "u-1",
        "secret": INTERNAL_SECRET,
    }


async def test_ex_post_risk_parses_a_full_response():
    body = _body()
    one_y = body["windows"][0]
    one_y["sharpe"] = _figure(
        0.84, frequency="weekly", q=52.0, n_obs=52, standard_error=0.31, flags=["short_window"]
    )
    one_y["var_99"] = _figure(
        None, flags=["insufficient_history", "tail_unstable"], reason="too few days"
    )

    result = await _ok(body).ex_post_risk(user_id="u-1")

    plain = RiskFigure(
        value=0.1,
        window_start=date(2025, 10, 16),
        window_end=date(2026, 10, 16),
        frequency="daily",
        q=252.0,
        n_obs=251,
        n_gaps=0,
        flags=(),
        reason=None,
        standard_error=None,
    )
    assert result.label == "ex-post · invested assets"
    assert result.base_date == date(2023, 1, 2)
    assert result.end_date == date(2026, 10, 16)
    assert result.rf_available is True
    assert [w.label for w in result.windows] == list(RISK_WINDOWS)
    assert result.excluded == (
        ExcludedHolding(ticker="7203.T", status="no_fx_rate", reason="no FX series for JPY"),
    )
    first = result.windows[0]
    assert first.start == date(2025, 10, 16)
    assert first.covered is True
    assert first.peak_date == date(2026, 2, 2)
    assert first.trough_date == date(2026, 4, 7)
    assert first.recovery_date is None
    assert first.max_days_under_water == 256
    assert first.figures["cagr"] == plain
    assert first.figures["sharpe"] == RiskFigure(
        value=0.84,
        window_start=date(2025, 10, 16),
        window_end=date(2026, 10, 16),
        frequency="weekly",
        q=52.0,
        n_obs=52,
        n_gaps=0,
        flags=("short_window",),
        reason=None,
        standard_error=0.31,
    )
    assert first.figures["var_99"].value is None
    assert first.figures["var_99"].reason == "too few days"
    assert first.figures["var_99"].flags == ("insufficient_history", "tail_unstable")
    assert list(first.figures) == list(RISK_FIGURES)


async def test_ex_post_risk_reads_an_integer_value_as_a_number():
    body = _body()
    body["windows"][0]["volatility"] = _figure(0)

    result = await _ok(body).ex_post_risk(user_id="u-1")

    assert result.windows[0].figures["volatility"].value == 0.0


async def test_ex_post_risk_with_nothing_to_measure():
    body = _body(base_date=None, end_date=None, rf_available=False, windows=[], n_excluded=0)
    body["excluded"] = []

    result = await _ok(body).ex_post_risk(user_id="u-1")

    assert result == ExPostRisk(
        label="ex-post · invested assets",
        base_date=None,
        end_date=None,
        rf_available=False,
        windows=(),
        excluded=(),
    )


async def test_ex_post_risk_keeps_an_uncovered_windows_greyed_figures():
    body = _body()
    body["windows"][3] = _window(
        "10Y",
        start="2016-10-16",
        covered=False,
        **{
            name: _figure(
                None,
                flags=["insufficient_history"],
                reason="insufficient history (portfolio history from 2023-01-02)",
            )
            for name in RISK_FIGURES
        },
        peak_date=None,
        trough_date=None,
        max_days_under_water=None,
    )

    result = await _ok(body).ex_post_risk(user_id="u-1")

    ten_y = result.windows[3]
    assert isinstance(ten_y, ExPostWindow)
    assert ten_y.covered is False
    assert ten_y.peak_date is None
    assert ten_y.max_days_under_water is None
    assert all(f.value is None for f in ten_y.figures.values())


def _without(key):
    body = _body()
    del body[key]
    return body


def _window_without(name):
    body = _body()
    del body["windows"][0][name]
    return body


def _figure_change(**changes):
    body = _body()
    body["windows"][0]["volatility"].update(changes)
    return body


def _windows(labels):
    body = _body()
    body["windows"] = [_window(label) for label in labels]
    return body


@pytest.mark.parametrize(
    "body",
    [
        _without("windows"),
        _without("rf_available"),
        _without("excluded"),
        _window_without("sortino"),
        _window_without("covered"),
        _window_without("recovery_date"),
        _figure_change(value="0.1"),
        _figure_change(value=True),
        _figure_change(n_obs=251.0),
        _figure_change(flags="tail_unstable"),
        _figure_change(window_start="not a date"),
        # The table has a column per window, in this order: one missing, extra or out of
        # place would put figures under the wrong heading.
        _windows(RISK_WINDOWS[:-1]),
        _windows(tuple(reversed(RISK_WINDOWS))),
        _windows(RISK_WINDOWS + ("20Y",)),
        {**_body(), "rf_available": "false"},
        [],
    ],
)
async def test_ex_post_risk_malformed_body_raises_portfolio_unavailable(body):
    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _ok(copy.deepcopy(body)).ex_post_risk(user_id="u-1")


@pytest.mark.parametrize("status_code", [404, 422, 500, 503])
async def test_ex_post_risk_error_status_raises_portfolio_unavailable(status_code):
    client = _client(lambda request: httpx.Response(status_code, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.ex_post_risk(user_id="u-1")


async def test_ex_post_risk_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(PortfolioUnavailable):
        await _client(handler).ex_post_risk(user_id="u-1")


async def test_ex_post_risk_non_json_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await client.ex_post_risk(user_id="u-1")
