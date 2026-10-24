"""HttpPortfolioClient.ex_ante_risk: portfolio's POST /risk/ex-ante (RK-17) -- the request it
sends, how σ_p, the decomposition, the constant-weight figures, the correlation matrix and the
FX decomposition (RK-26) are parsed, and which failures become which error. The JSON is written
out in the shape portfolio's ExAnteRiskResponse serves: figures are JSON numbers and fractions
(0.14 is 14%)."""

import copy
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    RISK_FIGURE_KEYS,
    BacktestAssetStatus,
    BenchmarkStatus,
    CorrelationCell,
    ExAnteHolding,
    ExAnteRisk,
    ExAnteWindow,
    FxDecompositionRow,
    RiskFigure,
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


def _holding(ticker, weight, pcr):
    return {
        "ticker": ticker,
        "weight": weight,
        "volatility": 0.2,
        "mcr": 0.19,
        "cr": 0.11,
        "pcr": pcr,
        "corr_with_portfolio": 0.96,
    }


_FX_FIGURES = ("local_volatility", "fx_volatility", "correlation", "eur_volatility")


def _fx_row(ticker="AAPL", currency="USD"):
    return {
        "ticker": ticker,
        "currency": currency,
        "local_volatility": _figure(0.24),
        "fx_volatility": _figure(0.08),
        "correlation": _figure(-0.2),
        "eur_volatility": _figure(0.2),
    }


def _body(**changes):
    return {
        "label": "ex-ante · constant weights",
        "currency": "eur",
        "rf_available": True,
        "window": {
            "label": "3Y",
            "requested_start": "2023-10-16",
            "start": "2023-10-20",
            "end": END,
            "limited_by": [],
        },
        "benchmark": {
            "ticker": "VWCE.DE",
            "available": True,
            "reason": None,
            "first_date": "2019-07-29",
        },
        "statuses": [
            {"ticker": "IWDA.AS", "status": "included", "reason": None},
            {"ticker": "AAPL", "status": "included", "reason": None},
            {"ticker": "7203.T", "status": "no_fx_rate", "reason": "no FX series for JPY"},
        ],
        "sigma_p": _figure(0.137),
        "diversification_ratio": _figure(1.16, standard_error=None),
        "effective_n": 1.92,
        "holdings": [_holding("IWDA.AS", 0.6, 0.84), _holding("AAPL", 0.4, 0.16)],
        "constant_weight": {
            **{name: _figure() for name in RISK_FIGURE_KEYS},
            "peak_date": "2024-02-02",
            "trough_date": "2024-04-07",
            "recovery_date": None,
            "max_days_under_water": 256,
        },
        "correlation": {
            "keys": ["IWDA.AS", "AAPL", "VWCE.DE"],
            "benchmark_key": "VWCE.DE",
            "cells": [
                {"a": a, "b": b, "figure": _figure(1.0 if a == b else 0.5)}
                for i, a in enumerate(["IWDA.AS", "AAPL", "VWCE.DE"])
                for b in ["IWDA.AS", "AAPL", "VWCE.DE"][i:]
            ],
        },
        "fx_decomposition": [_fx_row()],
        **changes,
    }


async def test_ex_ante_risk_posts_the_mix_with_the_secret():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json=_body())

    await _client(handler).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("0.6")), ("AAPL", Decimal("0.4"))],
        window="5Y",
        benchmark="IWDA.AS",
        exclude=["AAPL"],
    )

    assert captured == {
        "method": "POST",
        "path": "/risk/ex-ante",
        "body": {
            "assets": [{"ticker": "IWDA.AS", "weight": "0.6"}, {"ticker": "AAPL", "weight": "0.4"}],
            "window": "5Y",
            "benchmark": "IWDA.AS",
            "currency": "eur",
            "exclude": ["AAPL"],
        },
        "secret": INTERNAL_SECRET,
    }


async def test_ex_ante_risk_parses_a_full_response():
    result = await _ok(_body()).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("0.6"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert result.label == "ex-ante · constant weights"
    assert result.currency == "eur"
    assert result.rf_available is True
    assert result.window == ExAnteWindow(
        label="3Y",
        requested_start=date(2023, 10, 16),
        start=date(2023, 10, 20),
        end=date(2026, 10, 16),
        limited_by=(),
    )
    assert result.benchmark == BenchmarkStatus(
        ticker="VWCE.DE", available=True, reason=None, first_date=date(2019, 7, 29)
    )
    assert result.statuses[2] == BacktestAssetStatus(
        ticker="7203.T", status="no_fx_rate", reason="no FX series for JPY"
    )
    assert result.sigma_p == RiskFigure(
        value=0.137,
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
    assert result.diversification_ratio.value == 1.16
    assert result.effective_n == 1.92
    assert result.holdings[0] == ExAnteHolding(
        ticker="IWDA.AS",
        weight=0.6,
        volatility=0.2,
        mcr=0.19,
        cr=0.11,
        pcr=0.84,
        corr_with_portfolio=0.96,
    )
    constant = result.constant_weight
    assert list(constant.figures) == list(RISK_FIGURE_KEYS)
    assert constant.peak_date == date(2024, 2, 2)
    assert constant.recovery_date is None
    assert constant.max_days_under_water == 256
    matrix = result.correlation
    assert matrix.keys == ("IWDA.AS", "AAPL", "VWCE.DE")
    assert matrix.benchmark_key == "VWCE.DE"
    assert len(matrix.cells) == 6
    assert matrix.cells[1] == CorrelationCell(a="IWDA.AS", b="AAPL", figure=matrix.cells[1].figure)
    assert matrix.cells[1].figure.value == 0.5
    (fx,) = result.fx_decomposition
    assert isinstance(fx, FxDecompositionRow)
    assert (fx.ticker, fx.currency) == ("AAPL", "USD")
    assert (
        fx.local_volatility.value,
        fx.fx_volatility.value,
        fx.correlation.value,
        fx.eur_volatility.value,
    ) == (0.24, 0.08, -0.2, 0.2)
    assert fx.correlation.frequency == "weekly"


async def test_ex_ante_risk_an_all_eur_mix_has_an_empty_fx_decomposition():
    result = await _ok(_body(fx_decomposition=[])).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert result.fx_decomposition == ()


async def test_ex_ante_risk_a_greyed_decomposition_keeps_the_weights():
    body = _body()
    body["diversification_ratio"] = _figure(None, reason="insufficient history (52 of 104 weeks)")
    body["holdings"] = [
        {
            "ticker": "IWDA.AS",
            "weight": 1.0,
            **dict.fromkeys(("volatility", "mcr", "cr", "pcr", "corr_with_portfolio")),
        }
    ]

    result = await _ok(body).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="1Y", benchmark="VWCE.DE", exclude=[]
    )

    assert result.holdings == (ExAnteHolding("IWDA.AS", 1.0, None, None, None, None, None),)
    assert result.diversification_ratio.reason == "insufficient history (52 of 104 weeks)"


async def test_ex_ante_risk_histories_that_dont_overlap_have_no_start():
    body = _body()
    body["window"]["start"] = None
    body["window"]["limited_by"] = ["NEWCO.DE"]

    result = await _ok(body).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert result.window.start is None
    assert result.window.limited_by == ("NEWCO.DE",)


async def test_ex_ante_risk_with_nothing_included():
    body = _body(
        rf_available=False,
        statuses=[{"ticker": "IWDA.AS", "status": "excluded", "reason": "excluded by request"}],
        benchmark={
            "ticker": "VWCE.DE",
            "available": False,
            "reason": "not fetched: nothing to measure",
            "first_date": None,
        },
        **dict.fromkeys(
            (
                "window",
                "sigma_p",
                "diversification_ratio",
                "effective_n",
                "holdings",
                "constant_weight",
                "correlation",
                "fx_decomposition",
            )
        ),
    )

    result = await _ok(body).ex_ante_risk(
        assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
    )

    assert result == ExAnteRisk(
        label="ex-ante · constant weights",
        currency="eur",
        rf_available=False,
        window=None,
        benchmark=BenchmarkStatus(
            ticker="VWCE.DE",
            available=False,
            reason="not fetched: nothing to measure",
            first_date=None,
        ),
        statuses=(
            BacktestAssetStatus(ticker="IWDA.AS", status="excluded", reason="excluded by request"),
        ),
        sigma_p=None,
        diversification_ratio=None,
        effective_n=None,
        holdings=None,
        constant_weight=None,
        correlation=None,
        fx_decomposition=None,
    )


async def test_ex_ante_risk_rejected_mix_raises_invalid_request():
    client = _client(
        lambda request: httpx.Response(422, json={"detail": "exclude leaves nothing in the mix"})
    )

    with pytest.raises(InvalidPortfolioRequest, match="exclude leaves nothing"):
        await client.ex_ante_risk(
            assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
        )


def _without(key):
    body = _body()
    del body[key]
    return body


def _section_without(section, key):
    body = _body()
    del body[section][key]
    return body


def _fx(**changes):
    body = _body()
    body["fx_decomposition"] = [{**_fx_row(), **changes}]
    return body


def _fx_without(key):
    body = _body()
    del body["fx_decomposition"][0][key]
    return body


def _cells(cells):
    body = _body()
    body["correlation"]["cells"] = cells
    return body


@pytest.mark.parametrize(
    "body",
    [
        _without("holdings"),
        _without("correlation"),
        _without("constant_weight"),
        _without("statuses"),
        _without("effective_n"),
        _section_without("window", "limited_by"),
        _section_without("constant_weight", "var_95"),
        _section_without("constant_weight", "peak_date"),
        _section_without("correlation", "benchmark_key"),
        _without("fx_decomposition"),
        _fx_without("currency"),
        _fx_without("correlation"),
        # An FX figure is greyed with a reason, never null.
        _fx(eur_volatility=None),
        {**_body(), "effective_n": "1.9"},
        # A constant-weight figure is greyed with a reason, never null.
        {**_body(), "constant_weight": {**_body()["constant_weight"], "sharpe": None}},
        # A pair the matrix doesn't name, or a cell missing, would put a figure in the wrong
        # square or leave one blank.
        _cells(_body()["correlation"]["cells"][:-1]),
        _cells(
            [{**_body()["correlation"]["cells"][0], "b": "NOPE"}]
            + _body()["correlation"]["cells"][1:]
        ),
        [],
    ],
)
async def test_ex_ante_risk_malformed_body_raises_portfolio_unavailable(body):
    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _ok(copy.deepcopy(body)).ex_ante_risk(
            assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
        )


@pytest.mark.parametrize("status_code", [404, 500, 503])
async def test_ex_ante_risk_error_status_raises_portfolio_unavailable(status_code):
    client = _client(lambda request: httpx.Response(status_code, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.ex_ante_risk(
            assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
        )


async def test_ex_ante_risk_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(PortfolioUnavailable):
        await _client(handler).ex_ante_risk(
            assets=[("IWDA.AS", Decimal("1"))], window="3Y", benchmark="VWCE.DE", exclude=[]
        )
