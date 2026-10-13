"""HttpPortfolioClient.backtest: portfolio's POST /labs/backtest (LB-4) -- the request it sends,
how the period table, growth series and statuses are parsed, and which failures become which
error. The JSON is written out in the shape portfolio's BacktestResponse serves: every figure a
fixed-point string and a fraction (0.10 is +10%)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    BACKTEST_PERIODS,
    Backtest,
    BacktestAssetStatus,
    BacktestGrowth,
    BacktestPeriodReturn,
    BacktestRow,
)
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioUnavailable
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
END = "2026-10-09"


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


def _period(return_pct="0.01000000", *, start="2026-09-09", covered=True, gaps=0):
    return {"start": start, "end": END, "covered": covered, "return_pct": return_pct, "gaps": gaps}


def _row(ticker, weight, **overrides):
    periods = {p: _period() for p in BACKTEST_PERIODS}
    periods.update(overrides)
    return {"ticker": ticker, "weight": weight, "periods": periods}


def _body(**changes):
    return {
        "end_date": END,
        "mix": _row(None, "1"),
        "assets": [_row("VWCE.DE", "0.60000000"), _row("IWDA.AS", "0.40000000")],
        "growth": {
            "period": "1M",
            "dates": ["2026-09-09", "2026-09-10", END],
            "mix": ["0.00000000", "0.00400000", "0.01000000"],
            "assets": {
                "VWCE.DE": ["0.00000000", "0.00500000", "0.01200000"],
                "IWDA.AS": ["0.00000000", "0.00250000", "0.00700000"],
            },
        },
        "statuses": [
            {"ticker": "VWCE.DE", "status": "included", "reason": None},
            {"ticker": "IWDA.AS", "status": "included", "reason": None},
        ],
        **changes,
    }


def _ok(body):
    return _client(lambda request: httpx.Response(200, json=body))


async def test_backtest_posts_the_mix_with_the_secret():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["json"] = httpx.Response(200, content=request.content).json()
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json=_body())

    await _client(handler).backtest(
        assets=[("VWCE.DE", Decimal("60")), ("IWDA.AS", Decimal("40.5"))], period="1M"
    )

    # Weights travel as strings, so a Decimal never round-trips through a float.
    assert captured == {
        "method": "POST",
        "path": "/labs/backtest",
        "json": {
            "assets": [
                {"ticker": "VWCE.DE", "weight": "60"},
                {"ticker": "IWDA.AS", "weight": "40.5"},
            ],
            "period": "1M",
        },
        "secret": INTERNAL_SECRET,
    }


async def test_backtest_parses_a_full_response():
    result = await _ok(_body()).backtest(assets=[("VWCE.DE", Decimal("1"))], period="1M")

    one_percent = BacktestPeriodReturn(
        start=date(2026, 9, 9),
        end=date(2026, 10, 9),
        covered=True,
        return_pct=Decimal("0.01"),
        gaps=0,
    )
    periods = {p: one_percent for p in BACKTEST_PERIODS}
    assert result == Backtest(
        end_date=date(2026, 10, 9),
        mix=BacktestRow(ticker=None, weight=Decimal("1"), periods=periods),
        assets=(
            BacktestRow(ticker="VWCE.DE", weight=Decimal("0.6"), periods=periods),
            BacktestRow(ticker="IWDA.AS", weight=Decimal("0.4"), periods=periods),
        ),
        growth=BacktestGrowth(
            period="1M",
            dates=(date(2026, 9, 9), date(2026, 9, 10), date(2026, 10, 9)),
            mix=(Decimal("0"), Decimal("0.004"), Decimal("0.01")),
            assets={
                "VWCE.DE": (Decimal("0"), Decimal("0.005"), Decimal("0.012")),
                "IWDA.AS": (Decimal("0"), Decimal("0.0025"), Decimal("0.007")),
            },
        ),
        statuses=(
            BacktestAssetStatus(ticker="VWCE.DE", status="included", reason=None),
            BacktestAssetStatus(ticker="IWDA.AS", status="included", reason=None),
        ),
    )
    assert list(result.mix.periods) == list(BACKTEST_PERIODS)


async def test_backtest_keeps_an_asset_that_isnt_included_apart():
    body = _body(
        mix=_row(None, "1", **{"Max": _period(None, covered=False)}),
        assets=[_row("VWCE.DE", "1", **{"Max": _period(None, covered=False)})],
        growth={
            "period": "Max",
            "dates": ["2026-09-09", END],
            "mix": None,
            "assets": {"VWCE.DE": None},
        },
        statuses=[
            {"ticker": "VWCE.DE", "status": "included", "reason": None},
            {"ticker": "NEW.L", "status": "no_history", "reason": "no history yet"},
        ],
    )

    result = await _ok(body).backtest(
        assets=[("VWCE.DE", Decimal("1")), ("NEW.L", Decimal("1"))], period="Max"
    )

    assert [row.ticker for row in result.assets] == ["VWCE.DE"]
    assert result.statuses[1] == BacktestAssetStatus(
        ticker="NEW.L", status="no_history", reason="no history yet"
    )
    # Not covered is None, never 0: "Not enough history" must not read as a flat return.
    assert result.assets[0].periods["Max"].covered is False
    assert result.assets[0].periods["Max"].return_pct is None
    assert result.growth.mix is None
    assert result.growth.assets == {"VWCE.DE": None}


async def test_backtest_with_no_asset_included():
    body = {
        "end_date": None,
        "mix": None,
        "assets": [],
        "growth": None,
        "statuses": [{"ticker": "NOPE", "status": "not_tracked", "reason": "not tracked"}],
    }

    result = await _ok(body).backtest(assets=[("NOPE", Decimal("1"))], period="1Y")

    assert result == Backtest(
        end_date=None,
        mix=None,
        assets=(),
        growth=None,
        statuses=(BacktestAssetStatus(ticker="NOPE", status="not_tracked", reason="not tracked"),),
    )


async def test_backtest_rejected_mix_raises_invalid_portfolio_request_with_the_message():
    detail = [{"msg": "Value error, duplicate tickers in the mix: VWCE.DE", "loc": ["body"]}]
    client = _client(lambda request: httpx.Response(422, json={"detail": detail}))

    with pytest.raises(InvalidPortfolioRequest, match="duplicate tickers in the mix: VWCE.DE"):
        await client.backtest(
            assets=[("VWCE.DE", Decimal("1")), ("VWCE.DE", Decimal("1"))], period="1M"
        )


@pytest.mark.parametrize("status_code", [404, 500, 503])
async def test_backtest_any_other_error_status_raises_portfolio_unavailable(status_code):
    # A 404 included: this route has no account, so it must not become AccountNotFound.
    client = _client(lambda request: httpx.Response(status_code, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.backtest(assets=[("VWCE.DE", Decimal("1"))], period="1M")


async def test_backtest_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PortfolioUnavailable):
        await _client(handler).backtest(assets=[("VWCE.DE", Decimal("1"))], period="1M")


async def test_backtest_non_json_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(PortfolioUnavailable):
        await client.backtest(assets=[("VWCE.DE", Decimal("1"))], period="1M")


def _without(d, key):
    return {k: v for k, v in d.items() if k != key}


def _row_without_period(period):
    row = _row(None, "1")
    row["periods"] = _without(row["periods"], period)
    return row


def _growth(**changes):
    return {**_body()["growth"], **changes}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param([], id="body-is-a-list"),
        pytest.param(_without(_body(), "statuses"), id="missing-statuses"),
        pytest.param(_without(_body(), "growth"), id="missing-growth"),
        pytest.param(_body(mix=_row_without_period("Max")), id="row-missing-a-period"),
        pytest.param(
            _body(mix=_row(None, "1", **{"1M": _period(covered="true")})),
            id="covered-is-a-string",
        ),
        pytest.param(
            _body(mix=_row(None, "1", **{"1M": _period("lots")})), id="non-numeric-return"
        ),
        pytest.param(_body(assets=[_row("VWCE.DE", None)]), id="null-weight"),
        pytest.param(_body(end_date="not-a-date"), id="bad-end-date"),
        pytest.param(_body(growth=_growth(mix=["0", "0.004"])), id="short-mix-line"),
        pytest.param(_body(growth=_growth(assets={"VWCE.DE": ["0"] * 4})), id="long-asset-line"),
        pytest.param(
            _body(statuses=[{"ticker": "VWCE.DE", "status": "included"}]),
            id="status-missing-reason",
        ),
    ],
)
async def test_backtest_malformed_body_raises_portfolio_unavailable(body):
    with pytest.raises(PortfolioUnavailable):
        await _ok(body).backtest(assets=[("VWCE.DE", Decimal("1"))], period="1M")
