"""HttpPortfolioClient's rolling figures (RK-24, shown by RK-25): `rolling` on GET /risk/ex-post
and POST /risk/ex-ante, `rolling_correlation` on ex-ante, and the pair ex-ante sends for it. The
JSON is written out in the shape portfolio serves: each point is a full risk figure, a gap is a
figure with `value: null` and its reason, kept in place."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    RISK_FIGURE_KEYS,
    RISK_WINDOWS,
    RiskFigure,
    RollingCorrelation,
    RollingRisk,
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


def _figure(value=0.1, start="2023-10-20", end=END, **changes):
    return {
        "value": value,
        "window_start": start,
        "window_end": end,
        "frequency": "weekly",
        "q": 52.0,
        "n_obs": 52,
        "n_gaps": 0,
        "flags": [],
        "reason": None,
        "standard_error": None,
        **changes,
    }


GAP_REASON = "insufficient history (have 51, need 52)"


def _rolling():
    return {
        "window": "1Y",
        "step": "monthly",
        "volatility": [
            _figure(0.14, "2024-08-31", "2025-08-29"),
            _figure(None, "2024-09-30", "2025-09-30", n_obs=51, reason=GAP_REASON),
            _figure(0.15, "2025-10-16", END),
        ],
        "sharpe": [
            _figure(0.8, "2024-08-31", "2025-08-29", standard_error=0.6),
            _figure(None, "2024-09-30", "2025-09-30", n_obs=51, reason=GAP_REASON),
            _figure(0.9, "2025-10-16", END, standard_error=0.62),
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
        "rolling": _rolling(),
        **changes,
    }


def _ex_ante_body(**changes):
    """Nothing included, the shortest valid ex-ante body: the rolling keys are what's tested."""
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
        "rolling": _rolling(),
        "rolling_correlation": {
            "a": "IWDA.AS",
            "b": "portfolio",
            "window": "52W",
            "step": "weekly",
            "reason": None,
            "points": [_figure(0.97, "2024-10-18", "2025-10-17"), _figure(0.95)],
        },
        **changes,
    }


def _expected_figure(value, start, end, n_obs=52, reason=None, standard_error=None):
    return RiskFigure(
        value=value,
        window_start=date.fromisoformat(start),
        window_end=date.fromisoformat(end),
        frequency="weekly",
        q=52.0,
        n_obs=n_obs,
        n_gaps=0,
        flags=(),
        reason=reason,
        standard_error=standard_error,
    )


EXPECTED_ROLLING = RollingRisk(
    window="1Y",
    step="monthly",
    volatility=(
        _expected_figure(0.14, "2024-08-31", "2025-08-29"),
        _expected_figure(None, "2024-09-30", "2025-09-30", n_obs=51, reason=GAP_REASON),
        _expected_figure(0.15, "2025-10-16", END),
    ),
    sharpe=(
        _expected_figure(0.8, "2024-08-31", "2025-08-29", standard_error=0.6),
        _expected_figure(None, "2024-09-30", "2025-09-30", n_obs=51, reason=GAP_REASON),
        _expected_figure(0.9, "2025-10-16", END, standard_error=0.62),
    ),
)


async def test_ex_post_parses_rolling_volatility_and_sharpe_with_the_gap_in_place():
    risk = await _ok(_ex_post_body()).ex_post_risk(user_id="u1", benchmark="VWCE.DE")

    assert risk.rolling == EXPECTED_ROLLING


async def test_ex_post_rolling_is_none_when_portfolio_has_nothing_to_measure():
    body = _ex_post_body(base_date=None, end_date=None, windows=[], rolling=None)

    risk = await _ok(body).ex_post_risk(user_id="u1", benchmark="VWCE.DE")

    assert risk.rolling is None


async def test_ex_post_rolling_with_no_point_yet_is_empty_not_none():
    body = _ex_post_body(rolling={**_rolling(), "volatility": [], "sharpe": []})

    risk = await _ok(body).ex_post_risk(user_id="u1", benchmark="VWCE.DE")

    assert risk.rolling == RollingRisk(window="1Y", step="monthly", volatility=(), sharpe=())


async def test_ex_post_without_the_rolling_key_is_malformed():
    body = _ex_post_body()
    del body["rolling"]

    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _ok(body).ex_post_risk(user_id="u1", benchmark="VWCE.DE")


async def test_rolling_volatility_and_sharpe_of_different_lengths_is_malformed():
    # One chart per measure over the same windows: a missing point would shift every later one.
    body = _ex_post_body(rolling={**_rolling(), "sharpe": _rolling()["sharpe"][:2]})

    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _ok(body).ex_post_risk(user_id="u1", benchmark="VWCE.DE")


async def test_a_null_rolling_point_is_malformed():
    # A gap is a greyed figure kept in place, never a null: the chart needs its date and reason.
    rolling = _rolling()
    rolling["volatility"][1] = None

    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _ok(_ex_post_body(rolling=rolling)).ex_post_risk(user_id="u1", benchmark="VWCE.DE")


async def test_ex_ante_parses_rolling_and_the_pair_against_the_portfolio():
    risk = await _ok(_ex_ante_body()).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert risk.rolling == EXPECTED_ROLLING
    assert risk.rolling_correlation == RollingCorrelation(
        a="IWDA.AS",
        b="portfolio",
        window="52W",
        step="weekly",
        reason=None,
        points=(
            _expected_figure(0.97, "2024-10-18", "2025-10-17"),
            _expected_figure(0.95, "2023-10-20", END),
        ),
    )


async def test_ex_ante_pair_with_a_reason_has_no_points():
    pair = {
        "a": "7203.T",
        "b": "portfolio",
        "window": "52W",
        "step": "weekly",
        "reason": "7203.T left out: no FX series for JPY",
        "points": [],
    }

    risk = await _ok(_ex_ante_body(rolling_correlation=pair)).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert risk.rolling_correlation.points == ()
    assert risk.rolling_correlation.reason == "7203.T left out: no FX series for JPY"


async def test_ex_ante_rolling_keys_are_none_when_nothing_was_included():
    body = _ex_ante_body(rolling=None, rolling_correlation=None)

    risk = await _ok(body).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert risk.rolling is None
    assert risk.rolling_correlation is None


@pytest.mark.parametrize("key", ["rolling", "rolling_correlation"])
async def test_ex_ante_without_a_rolling_key_is_malformed(key):
    body = _ex_ante_body()
    del body[key]

    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _ok(body).ex_ante_risk(
            assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
        )


@pytest.mark.parametrize(
    "pair, sent",
    [
        (("IWDA.AS", None), {"a": "IWDA.AS", "b": None}),
        (("IWDA.AS", "VWCE.DE"), {"a": "IWDA.AS", "b": "VWCE.DE"}),
    ],
)
async def test_ex_ante_sends_the_rolling_pair(pair, sent):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ex_ante_body())

    await _client(handler).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))],
        window="3Y",
        benchmark="VWCE.DE",
        exclude=[],
        rolling_pair=pair,
    )

    assert captured["body"]["rolling_correlation"] == sent


async def test_ex_ante_sends_no_pair_unless_asked():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ex_ante_body(rolling_correlation=None))

    await _client(handler).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert "rolling_correlation" not in captured["body"]
