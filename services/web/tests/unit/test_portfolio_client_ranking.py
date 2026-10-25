"""HttpPortfolioClient.rank_candidates: portfolio's POST /risk/what-if/ranking (RK-29) -- the
request it sends, how the window, ranked, greyed and unranked candidates are parsed, and which
failures become which error. The JSON is written out in the shape portfolio's RankingResponse
serves: figures are JSON numbers and fractions (0.14 is 14%)."""

import copy
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    RANKING_ROW_KEYS,
    BacktestAssetStatus,
    CandidateRanking,
    RankedCandidate,
    RankingWindow,
    UnrankedCandidate,
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


def _ranked(ticker, delta_sigma, **changes):
    return {
        "ticker": ticker,
        "name": f"{ticker} name",
        "history_start": "2009-09-25",
        "n_weeks": 890,
        "sigma_p": _row(0.14, 0.14 + delta_sigma, delta_sigma),
        "diversification_ratio": _row(1.16, 1.25, 0.09),
        "candidate_pcr": _row(None, 0.03, None),
        "candidate_correlation": _row(None, 0.12, None),
        "sharpe": {**_row(0.8, 0.88, 0.08), "delta_se": 0.1},
        **changes,
    }


def _body(**changes):
    return {
        "label": "ex-ante · constant weights",
        "rf_available": True,
        "statuses": [
            {"ticker": "IWDA.AS", "status": "included", "reason": None},
            {"ticker": "7203.T", "status": "no_fx_rate", "reason": "no FX series for JPY"},
        ],
        "nothing": None,
        "universe": 52,
        "cap": 50,
        "over_cap": ["ZPRV.DE", "ZPRX.DE"],
        "weight": 0.05,
        "rank_by": "sigma_p",
        "window": {
            "label": "3Y",
            "requested_start": "2023-10-16",
            "start": "2023-10-13",
            "end": END,
        },
        "ranked": [_ranked("EGLN.L", -0.002), _ranked("IUSN.DE", 0.001)],
        "greyed": [
            _ranked(
                "GAPPY.L",
                0.0,
                diversification_ratio={
                    "before": _figure(None, reason="insufficient history (have 96, need 104)"),
                    "after": _figure(None, reason="insufficient history (have 96, need 104)"),
                    "delta": None,
                },
            )
        ],
        "unranked": [
            {
                "ticker": "LATE.DE",
                "name": "LATE.DE name",
                "status": "short_history",
                "reason": "insufficient history (have 80, need 156)",
                "history_start": "2025-03-07",
                "n_weeks": 80,
            },
            {
                "ticker": "0700.HK",
                "name": "Tencent",
                "status": "no_fx_rate",
                "reason": "no FX series for HKD",
                "history_start": None,
                "n_weeks": None,
            },
        ],
        **changes,
    }


def _call(client, **changes):
    kwargs = {
        "assets": [("IWDA.AS", Decimal("1"))],
        "weight": Decimal("0.05"),
        "window": "3Y",
        "exclude": [],
        "rank_by": "sigma_p",
        **changes,
    }
    return client.rank_candidates(**kwargs)


async def test_rank_candidates_posts_the_mix_and_weight_with_the_secret():
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["secret"] = request.headers["x-internal-secret"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_body())

    await _call(
        _client(handler),
        assets=[("IWDA.AS", Decimal("0.6")), ("7203.T", Decimal("0.4"))],
        weight=Decimal("0.075"),
        window="5Y",
        exclude=["7203.T"],
        rank_by="diversification_ratio",
    )

    assert (captured["method"], captured["path"]) == ("POST", "/risk/what-if/ranking")
    assert captured["secret"] == INTERNAL_SECRET
    assert captured["body"] == {
        "assets": [
            {"ticker": "IWDA.AS", "weight": "0.6"},
            {"ticker": "7203.T", "weight": "0.4"},
        ],
        "weight": "0.075",
        "window": "5Y",
        "exclude": ["7203.T"],
        "rank_by": "diversification_ratio",
    }


async def test_rank_candidates_parses_a_full_response():
    ranking = await _call(_ok(_body()))

    assert isinstance(ranking, CandidateRanking)
    assert ranking.label == "ex-ante · constant weights"
    assert ranking.rf_available is True
    assert ranking.statuses[1] == BacktestAssetStatus(
        "7203.T", "no_fx_rate", "no FX series for JPY"
    )
    assert ranking.nothing is None
    assert (ranking.universe, ranking.cap, ranking.over_cap) == (52, 50, ("ZPRV.DE", "ZPRX.DE"))
    assert (ranking.weight, ranking.rank_by) == (0.05, "sigma_p")
    assert ranking.window == RankingWindow(
        "3Y", date(2023, 10, 16), date(2023, 10, 13), date(2026, 10, 16)
    )

    first = ranking.ranked[0]
    assert isinstance(first, RankedCandidate)
    assert (first.ticker, first.name) == ("EGLN.L", "EGLN.L name")
    assert (first.history_start, first.n_weeks) == (date(2009, 9, 25), 890)
    assert tuple(first.rows) == RANKING_ROW_KEYS
    assert first.rows["sigma_p"].delta == pytest.approx(-0.002)
    assert first.rows["candidate_pcr"].before is None
    assert first.rows["sharpe"].delta_se == 0.1
    assert first.rows["sigma_p"].delta_se is None
    assert [c.ticker for c in ranking.ranked] == ["EGLN.L", "IUSN.DE"]

    (greyed,) = ranking.greyed
    assert greyed.rows["diversification_ratio"].delta is None
    assert greyed.rows["diversification_ratio"].after.reason.startswith("insufficient history")

    assert ranking.unranked == (
        UnrankedCandidate(
            "LATE.DE",
            "LATE.DE name",
            "short_history",
            "insufficient history (have 80, need 156)",
            date(2025, 3, 7),
            80,
        ),
        UnrankedCandidate("0700.HK", "Tencent", "no_fx_rate", "no FX series for HKD", None, None),
    )


async def test_rank_candidates_with_nothing_to_rank():
    body = _body(
        nothing="no tracked ticker outside the mix to rank",
        universe=0,
        over_cap=[],
        window=None,
        ranked=None,
        greyed=None,
        unranked=[],
    )

    ranking = await _call(_ok(body))

    assert ranking.nothing == "no tracked ticker outside the mix to rank"
    assert (ranking.window, ranking.ranked, ranking.greyed, ranking.unranked) == (
        None,
        None,
        None,
        (),
    )


async def test_rank_candidates_rejected_request_raises_invalid_request():
    client = _client(lambda request: httpx.Response(422, json={"detail": "needs 3Y or more"}))

    with pytest.raises(InvalidPortfolioRequest, match="3Y or more"):
        await _call(client)


def _without(key):
    body = _body()
    del body[key]
    return body


def _ranked_with(**changes):
    return _body(ranked=[_ranked("EGLN.L", -0.002, **changes)])


@pytest.mark.parametrize(
    "body",
    [
        _without("ranked"),
        _without("unranked"),
        _without("over_cap"),
        _without("nothing"),
        _body(universe="52"),
        _body(over_cap="ZPRV.DE"),
        _body(rf_available="true"),
        _ranked_with(n_weeks=890.5),
        _ranked_with(history_start=None),
        # An unknown or missing row would be dropped from the table without a word.
        _ranked_with(max_drawdown=_row(-0.2, -0.19, 0.01)),
        {**_body(), "ranked": [{k: v for k, v in _ranked("A", 0.0).items() if k != "sharpe"}]},
        # An "after" figure is greyed with a reason, never null.
        _ranked_with(sigma_p=_row(0.14, 0.1, 0) | {"after": None}),
        _body(unranked=[{"ticker": "X", "name": "X", "status": "failed", "reason": "boom"}]),
        [],
    ],
)
async def test_rank_candidates_malformed_body_raises_portfolio_unavailable(body):
    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _call(_ok(copy.deepcopy(body)))


@pytest.mark.parametrize("status_code", [404, 500, 502, 503])
async def test_rank_candidates_error_status_raises_portfolio_unavailable(status_code):
    client = _client(lambda request: httpx.Response(status_code, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await _call(client)


async def test_rank_candidates_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(PortfolioUnavailable):
        await _call(_client(handler))
