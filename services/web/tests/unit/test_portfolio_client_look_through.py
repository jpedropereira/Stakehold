"""HttpPortfolioClient.look_through: portfolio's POST /risk/look-through (RK-22) -- the request
it sends, how the assets, snapshots, coverage, dimensions and names are parsed, and which
failures become which error. The JSON is written out in the shape portfolio's
LookThroughResponse serves: weights are fixed-point string fractions ("0.60000000" is 60%)."""

import copy
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    LOOK_THROUGH_DIMENSIONS,
    LookThrough,
    LookThroughAsset,
    LookThroughBucket,
    LookThroughConcentration,
    LookThroughName,
    LookThroughSnapshot,
)
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioUnavailable
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
ASSETS = [("IWDA.AS", Decimal("0.6")), ("BRK-B", Decimal("0.4"))]


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


def _ok(body):
    return _client(lambda request: httpx.Response(200, json=body))


def _concentration(top1="0.85000000", **changes):
    return {
        "top_shares": {"1": top1, "5": "1.00000000", "10": "1.00000000"},
        "known_share": "1.00000000",
        "herfindahl": "0.74500000",
        "effective_n": "1.34228188",
        "reason": None,
        **changes,
    }


def _dimension(label, *buckets):
    return {
        "label": label,
        "buckets": [{"label": b, "weight": w, "kind": k} for b, w, k in buckets],
        "concentration": _concentration(),
    }


def _body(**changes):
    return {
        "assets": [
            {
                "ticker": "IWDA.AS",
                "status": "included",
                "reason": None,
                "kind": "look_through",
                "weight": "0.60000000",
                "matched_in": None,
                "note": None,
            },
            {
                "ticker": "BRK-B",
                "status": "included",
                "reason": None,
                "kind": "direct",
                "weight": "0.40000000",
                "matched_in": "IWDA.AS",
                "note": None,
            },
        ],
        "exposures": {
            "snapshots": [
                {
                    "ticker": "IWDA.AS",
                    "fund_key": "world",
                    "as_of_date": "2026-08-30",
                    "n_lines": 2,
                    "raw_weight_sum": "100.03000000",
                }
            ],
            "coverage": {
                "look_through": "0.60000000",
                "direct_matched": "0.40000000",
                "direct_unmatched": "0.00000000",
                "commodity": "0.00000000",
                "unmapped": "0.00000000",
            },
            "dimensions": {
                "currency": _dimension(
                    "listing currency of holdings",
                    ("USD", "0.85000000", "known"),
                    ("EUR", "0.15000000", "known"),
                ),
                "trading_currency": _dimension(
                    "trading currency of positions",
                    ("EUR", "0.60000000", "known"),
                    ("USD", "0.40000000", "known"),
                ),
                "sector": _dimension(
                    "sector",
                    ("Financials", "0.85000000", "known"),
                    ("Information Technology", "0.15000000", "known"),
                ),
                "country": _dimension(
                    "country",
                    ("United States", "0.85000000", "known"),
                    ("Germany", "0.15000000", "known"),
                ),
                "asset_class": _dimension("asset class", ("Equity", "1.00000000", "known")),
            },
            "names": {
                "top": [
                    {
                        "issuer": "BERKSHIRE HATHAWAY INC",
                        "location": "United States",
                        "weight": "0.85000000",
                        "sources": ["IWDA.AS", "BRK-B"],
                    },
                    {
                        "issuer": "SAP",
                        "location": "Germany",
                        "weight": "0.15000000",
                        "sources": ["IWDA.AS"],
                    },
                ],
                "n_names": 2,
                "concentration": _concentration(),
            },
        },
        **changes,
    }


async def test_look_through_posts_the_mix_with_the_secret():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json=_body())

    await _client(handler).look_through(assets=ASSETS)

    assert captured == {
        "method": "POST",
        "path": "/risk/look-through",
        "body": {
            "assets": [{"ticker": "IWDA.AS", "weight": "0.6"}, {"ticker": "BRK-B", "weight": "0.4"}]
        },
        "secret": INTERNAL_SECRET,
    }


async def test_look_through_parses_a_full_response():
    result = await _ok(_body()).look_through(assets=ASSETS)

    assert result.assets[1] == LookThroughAsset(
        ticker="BRK-B",
        status="included",
        reason=None,
        kind="direct",
        weight=Decimal("0.4"),
        matched_in="IWDA.AS",
        note=None,
    )
    exposures = result.exposures
    assert exposures.snapshots == (
        LookThroughSnapshot(
            ticker="IWDA.AS",
            fund_key="world",
            as_of_date=date(2026, 8, 30),
            n_lines=2,
            raw_weight_sum=Decimal("100.03"),
        ),
    )
    assert exposures.coverage == {
        "look_through": Decimal("0.6"),
        "direct_matched": Decimal("0.4"),
        "direct_unmatched": Decimal(0),
        "commodity": Decimal(0),
        "unmapped": Decimal(0),
    }
    assert tuple(exposures.dimensions) == LOOK_THROUGH_DIMENSIONS
    currency = exposures.dimensions["currency"]
    assert currency.label == "listing currency of holdings"
    assert currency.buckets == (
        LookThroughBucket("USD", Decimal("0.85"), "known"),
        LookThroughBucket("EUR", Decimal("0.15"), "known"),
    )
    assert currency.concentration == LookThroughConcentration(
        top_shares={1: Decimal("0.85"), 5: Decimal(1), 10: Decimal(1)},
        known_share=Decimal(1),
        herfindahl=Decimal("0.745"),
        effective_n=Decimal("1.34228188"),
        reason=None,
    )
    assert exposures.names.top[0] == LookThroughName(
        issuer="BERKSHIRE HATHAWAY INC",
        location="United States",
        weight=Decimal("0.85"),
        sources=("IWDA.AS", "BRK-B"),
    )
    assert exposures.names.n_names == 2


async def test_look_through_with_nothing_known_greys_the_concentration():
    body = _body()
    body["exposures"]["dimensions"]["sector"]["concentration"] = _concentration(
        top1="0.00000000",
        known_share="0.00000000",
        herfindahl=None,
        effective_n=None,
        reason="no known exposure: every holding is unmapped or unknown",
    )

    result = await _ok(body).look_through(assets=ASSETS)

    concentration = result.exposures.dimensions["sector"].concentration
    assert concentration.herfindahl is None
    assert concentration.effective_n is None
    assert concentration.reason == "no known exposure: every holding is unmapped or unknown"


async def test_look_through_with_nothing_included():
    body = _body(
        assets=[
            {
                "ticker": "NOPE",
                "status": "not_tracked",
                "reason": "not tracked",
                "kind": None,
                "weight": None,
                "matched_in": None,
                "note": None,
            }
        ],
        exposures=None,
    )

    result = await _ok(body).look_through(assets=[("NOPE", Decimal("1"))])

    assert result == LookThrough(
        assets=(LookThroughAsset("NOPE", "not_tracked", "not tracked", None, None, None, None),),
        exposures=None,
    )


async def test_look_through_rejected_mix_raises_invalid_request():
    client = _client(lambda request: httpx.Response(422, json={"detail": "duplicate ticker"}))

    with pytest.raises(InvalidPortfolioRequest, match="duplicate ticker"):
        await client.look_through(assets=ASSETS)


def _without(*path):
    body = _body()
    target = body
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return body


def _with(value, *path):
    body = _body()
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return body


@pytest.mark.parametrize(
    "body",
    [
        _without("assets"),
        _without("exposures"),
        _without("exposures", "snapshots"),
        _without("exposures", "coverage", "unmapped"),
        _without("exposures", "names", "n_names"),
        _without("exposures", "dimensions", "currency", "label"),
        _without("exposures", "dimensions", "sector", "concentration", "known_share"),
        # A dimension missing, or one web doesn't know, would drop or misplace a table.
        _without("exposures", "dimensions", "country"),
        _with(_body()["exposures"]["dimensions"]["sector"], "exposures", "dimensions", "industry"),
        _with("lots", "exposures", "coverage", "unmapped"),
        _with(0.85, "exposures", "names", "top", 0, "weight"),
        _with("abc", "exposures", "dimensions", "sector", "concentration", "top_shares", "one"),
        [],
    ],
)
async def test_look_through_malformed_body_raises_portfolio_unavailable(body):
    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _ok(copy.deepcopy(body)).look_through(assets=ASSETS)


@pytest.mark.parametrize("status_code", [404, 500, 502, 503])
async def test_look_through_error_status_raises_portfolio_unavailable(status_code):
    client = _client(lambda request: httpx.Response(status_code, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.look_through(assets=ASSETS)


async def test_look_through_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(PortfolioUnavailable):
        await _client(handler).look_through(assets=ASSETS)
