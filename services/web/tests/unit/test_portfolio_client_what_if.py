"""HttpPortfolioClient.what_if: portfolio's POST /risk/what-if (RK-19) -- the request it sends,
how the candidate, funding, window, before/after rows and threshold are parsed, and which
failures become which error. The JSON is written out in the shape portfolio's WhatIfResponse
serves: figures are JSON numbers and fractions (0.14 is 14%)."""

import copy
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    WHAT_IF_ROW_KEYS,
    BacktestAssetStatus,
    RiskFigure,
    WhatIf,
    WhatIfCandidate,
    WhatIfFunding,
    WhatIfThreshold,
    WhatIfWindow,
)
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioUnavailable
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
        "window_start": "2023-10-20",
        "window_end": END,
        "frequency": "weekly",
        "q": 52.0,
        "n_obs": 156,
        "n_gaps": 0,
        "flags": [],
        "reason": None,
        "standard_error": None,
        **changes,
    }


def _row(before, after, delta):
    return {
        "before": None if before is None else _figure(before),
        "after": _figure(after),
        "delta": delta,
    }


def _body(**changes):
    return {
        "label": "ex-ante · constant weights",
        "rf_available": True,
        "statuses": [
            {"ticker": "IWDA.AS", "status": "included", "reason": None},
            {"ticker": "7203.T", "status": "no_fx_rate", "reason": "no FX series for JPY"},
        ],
        "candidate": {
            "ticker": "EGLN.L",
            "status": "included",
            "reason": None,
            "requested_weight": 0.1,
            "weight": 0.1,
            "capped": False,
            "survivorship": False,
        },
        "funding": {"rule": "pro_rata", "ticker": None, "reason": None},
        "window": {
            "label": "3Y",
            "requested_start": "2023-10-16",
            "start": "2023-10-20",
            "end": END,
            "limited_by": [],
            "shortened": False,
            "start_without_candidate": "2023-10-20",
            "end_without_candidate": END,
        },
        "weights": {"before": {"IWDA.AS": 1.0}, "after": {"IWDA.AS": 0.9, "EGLN.L": 0.1}},
        "rows": {
            "sigma_p": _row(0.14, 0.138, -0.002),
            "diversification_ratio": _row(1.16, 1.25, 0.09),
            "candidate_pcr": _row(None, 0.03, None),
            "candidate_correlation": _row(None, 0.12, None),
            "sharpe": {**_row(0.8, 0.88, 0.08), "delta_se": 0.1},
            "max_drawdown": _row(-0.2, -0.19, 0.01),
        },
        "threshold": {
            "applicable": True,
            "reason": None,
            "candidate_sharpe": _figure(0.5, standard_error=0.58),
            "correlation": 0.12,
            "portfolio_sharpe": 0.8,
            "threshold": 0.096,
            "improves": True,
        },
        **changes,
    }


def _call(client, **changes):
    kwargs = {
        "assets": [("IWDA.AS", Decimal("1"))],
        "candidate": ("EGLN.L", Decimal("0.1")),
        "funding": "pro_rata",
        "window": "3Y",
        "exclude": [],
        **changes,
    }
    return client.what_if(**kwargs)


async def test_what_if_posts_the_mix_and_candidate_with_the_secret():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json=_body())

    await _call(
        _client(handler),
        assets=[("IWDA.AS", Decimal("0.6")), ("AAPL", Decimal("0.4"))],
        candidate=("EGLN.L", Decimal("0.075")),
        funding="replace:AAPL",
        window="5Y",
        exclude=["7203.T"],
    )

    # No benchmark: the what-if has none (RK-19).
    assert captured == {
        "method": "POST",
        "path": "/risk/what-if",
        "body": {
            "assets": [{"ticker": "IWDA.AS", "weight": "0.6"}, {"ticker": "AAPL", "weight": "0.4"}],
            "candidate": {"ticker": "EGLN.L", "weight": "0.075"},
            "funding": "replace:AAPL",
            "window": "5Y",
            "exclude": ["7203.T"],
        },
        "secret": INTERNAL_SECRET,
    }


async def test_what_if_parses_a_full_response():
    result = await _call(_ok(_body()))

    assert result.label == "ex-ante · constant weights"
    assert result.rf_available is True
    assert result.statuses[1] == BacktestAssetStatus(
        ticker="7203.T", status="no_fx_rate", reason="no FX series for JPY"
    )
    assert result.candidate == WhatIfCandidate(
        ticker="EGLN.L",
        status="included",
        reason=None,
        requested_weight=0.1,
        weight=0.1,
        capped=False,
        survivorship=False,
    )
    assert result.funding == WhatIfFunding(rule="pro_rata", ticker=None, reason=None)
    assert result.window == WhatIfWindow(
        label="3Y",
        requested_start=date(2023, 10, 16),
        start=date(2023, 10, 20),
        end=date(2026, 10, 16),
        limited_by=(),
        shortened=False,
        start_without_candidate=date(2023, 10, 20),
        end_without_candidate=date(2026, 10, 16),
    )
    assert list(result.rows) == list(WHAT_IF_ROW_KEYS)
    sigma = result.rows["sigma_p"]
    assert sigma.before == RiskFigure(
        value=0.14,
        window_start=date(2023, 10, 20),
        window_end=date(2026, 10, 16),
        frequency="weekly",
        q=52.0,
        n_obs=156,
        n_gaps=0,
        flags=(),
        reason=None,
        standard_error=None,
    )
    assert (sigma.after.value, sigma.delta, sigma.delta_se) == (0.138, -0.002, None)
    pcr = result.rows["candidate_pcr"]
    assert (pcr.before, pcr.after.value, pcr.delta) == (None, 0.03, None)
    sharpe = result.rows["sharpe"]
    assert (sharpe.delta, sharpe.delta_se) == (0.08, 0.1)
    assert result.threshold == WhatIfThreshold(
        applicable=True,
        reason=None,
        candidate_sharpe=result.threshold.candidate_sharpe,
        correlation=0.12,
        portfolio_sharpe=0.8,
        threshold=0.096,
        improves=True,
    )
    assert result.threshold.candidate_sharpe.standard_error == 0.58


async def test_what_if_under_replace_has_no_threshold_figures():
    body = _body(
        candidate={**_body()["candidate"], "weight": 0.05, "capped": True, "survivorship": True},
        funding={"rule": "replace", "ticker": "AAPL", "reason": None},
        threshold={
            "applicable": False,
            "reason": "shown under pro-rata funding only",
            **dict.fromkeys(
                ("candidate_sharpe", "correlation", "portfolio_sharpe", "threshold", "improves")
            ),
        },
    )

    result = await _call(_ok(body), funding="replace:AAPL")

    assert (result.candidate.weight, result.candidate.capped) == (0.05, True)
    assert result.candidate.survivorship is True
    assert result.funding == WhatIfFunding(rule="replace", ticker="AAPL", reason=None)
    assert result.threshold == WhatIfThreshold(
        applicable=False,
        reason="shown under pro-rata funding only",
        candidate_sharpe=None,
        correlation=None,
        portfolio_sharpe=None,
        threshold=None,
        improves=None,
    )


async def test_what_if_with_nothing_to_compare():
    body = _body(
        rf_available=False,
        candidate={
            **_body()["candidate"],
            "status": "no_fx_rate",
            "reason": "no FX series for HKD",
            "weight": None,
            "capped": None,
        },
        **dict.fromkeys(("window", "weights", "rows", "threshold")),
    )

    result = await _call(_ok(body))

    assert result == WhatIf(
        label="ex-ante · constant weights",
        rf_available=False,
        statuses=(
            BacktestAssetStatus(ticker="IWDA.AS", status="included", reason=None),
            BacktestAssetStatus(
                ticker="7203.T", status="no_fx_rate", reason="no FX series for JPY"
            ),
        ),
        candidate=WhatIfCandidate(
            ticker="EGLN.L",
            status="no_fx_rate",
            reason="no FX series for HKD",
            requested_weight=0.1,
            weight=None,
            capped=None,
            survivorship=False,
        ),
        funding=WhatIfFunding(rule="pro_rata", ticker=None, reason=None),
        window=None,
        rows=None,
        threshold=None,
    )


async def test_what_if_rejected_request_raises_invalid_request():
    client = _client(
        lambda request: httpx.Response(422, json={"detail": "candidate is already in the mix"})
    )

    with pytest.raises(InvalidPortfolioRequest, match="already in the mix"):
        await _call(client)


def _without(key):
    body = _body()
    del body[key]
    return body


def _section_without(section, key):
    body = _body()
    del body[section][key]
    return body


@pytest.mark.parametrize(
    "body",
    [
        _without("candidate"),
        _without("funding"),
        _without("rows"),
        _without("threshold"),
        _section_without("candidate", "survivorship"),
        _section_without("window", "shortened"),
        _section_without("rows", "max_drawdown"),
        _section_without("threshold", "improves"),
        {**_body(), "candidate": {**_body()["candidate"], "survivorship": "false"}},
        {**_body(), "candidate": {**_body()["candidate"], "capped": "no"}},
        {**_body(), "candidate": {**_body()["candidate"], "requested_weight": "0.1"}},
        # An "after" figure is greyed with a reason, never null.
        {**_body(), "rows": {**_body()["rows"], "sigma_p": _row(0.14, 0.1, 0) | {"after": None}}},
        [],
    ],
)
async def test_what_if_malformed_body_raises_portfolio_unavailable(body):
    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _call(_ok(copy.deepcopy(body)))


@pytest.mark.parametrize("status_code", [404, 500, 503])
async def test_what_if_error_status_raises_portfolio_unavailable(status_code):
    client = _client(lambda request: httpx.Response(status_code, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await _call(client)


async def test_what_if_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(PortfolioUnavailable):
        await _call(_client(handler))
