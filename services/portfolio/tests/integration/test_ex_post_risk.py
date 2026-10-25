"""Ex-post risk (RK-11) against live Postgres: risk/flows.compute_ex_post over the real TWR
reads, and GET /risk/ex-post through a real create_app() -- the secret check and the JSON shape.

Seeds through the real CLI stack, exactly as test_portfolio_twr.py does (its helpers are
repeated here: test modules can't import each other). The history is three days, so only the
Max window is measured, and only its cumulative return has enough history for a value: the
point is the wiring, not the figures, which the unit suite pins. The benchmark (RK-13) is the
same IWDA.AS series, named per request; the stub serves no other prices. Two regimes (RK-28):
one inside the history and one ongoing that started before it.
"""

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from portfolio import cli, jobs
from portfolio.api.app import create_app
from portfolio.benchmark.base import BenchmarkStatus
from portfolio.datastore_client.base import (
    PricePoint,
    Regime,
    RegimeList,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
from portfolio.risk import flows
from portfolio.settings import Settings

pytestmark = pytest.mark.integration

_ISIN = "IE00B4L5Y983"
_SECRET = "integration-test-secret"  # pragma: allowlist secret
D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)
TODAY = date(2026, 1, 9)

# IWDA.AS: 100, then +5% to 105, then -2% to 102.9.
_PRICES = {
    "IWDA.AS": [
        PricePoint(D1, Decimal("100.00"), None),
        PricePoint(D2, Decimal("105.00"), Decimal("0.05")),
        PricePoint(D3, Decimal("102.90"), Decimal("-0.02")),
    ]
}


class StubDataStoreClient:
    def get_price_series(self, ticker, start, end):
        return [p for p in _PRICES.get(ticker, []) if start <= p.date <= end]

    def get_fx_rate_series(self, base, quote, start, end):
        return []

    def get_ticker_info(self, ticker):
        return TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")

    def get_risk_free_series(self, start, end):
        return RiskFreeSeries(
            first_date=date(1999, 1, 4),
            points=[RiskFreePoint(date(2025, 12, 31), Decimal("1.9300"), "estr")],
        )

    def get_regimes(self, start, end):
        return RegimeList(
            94,
            [
                Regime(
                    "era",
                    "monetary_policy",
                    "EA",
                    "Era",
                    date(2024, 6, 12),
                    None,
                    None,
                    None,
                    "manual",
                    None,
                ),
                Regime(
                    "dip", "drawdown", "SPX", "Dip", D2, D3, None, "mild", "manual_approx", None
                ),
            ],
        )


@pytest.fixture(autouse=True)
def _ticker_mappings(seed_mapping):
    seed_mapping("ISIN", _ISIN, "IWDA.AS")


@pytest.fixture(autouse=True)
def _serve_prices(monkeypatch):
    monkeypatch.setenv("DATASTORE_API_URL", "http://stub-datastore")
    monkeypatch.setenv("INTERNAL_API_SECRET", "stub-secret")  # pragma: allowlist secret
    monkeypatch.setattr(
        jobs, "HttpDataStoreClient", lambda base_url, internal_api_secret: StubDataStoreClient()
    )


def _add_account(user_id, label):
    assert (
        cli.main(["account", "add", "--user", str(user_id), "--broker", "acme", "--label", label])
        == 0
    )


def _trade(user_id, label, *, ref, kind, when, quantity, price, amount):
    exit_code = cli.main(
        [
            "add-transaction",
            "--user",
            str(user_id),
            "--account",
            label,
            "--ref",
            ref,
            "--type",
            kind,
            "--datetime",
            when,
            "--currency",
            "EUR",
            "--instrument",
            _ISIN,
            "--instrument-kind",
            "ISIN",
            "--quantity",
            quantity,
            "--price",
            price,
            "--amount",
            amount,
        ]
    )
    assert exit_code == 0


def _seed():
    """120 IWDA in account A and 20 in B, all bought on D1. A buys 60 more on D2, which must
    not move the return."""
    owner = uuid.uuid4()
    _add_account(owner, "A")
    _add_account(owner, "B")
    d1, d2 = "2026-01-05T10:00:00+00:00", "2026-01-06T10:00:00+00:00"
    _trade(
        owner, "A", ref="a", kind="BUY", when=d1, quantity="120", price="100.00", amount="-12000.00"
    )
    _trade(
        owner, "B", ref="b", kind="BUY", when=d1, quantity="20", price="100.00", amount="-2000.00"
    )
    _trade(
        owner, "A", ref="a2", kind="BUY", when=d2, quantity="60", price="105.00", amount="-6300.00"
    )
    return owner


def test_the_twr_from_two_accounts_is_measured_over_the_standard_windows(pg_connect):
    owner = _seed()

    with pg_connect() as conn:
        result = flows.compute_ex_post(
            conn, StubDataStoreClient(), user_id=owner, benchmark="IWDA.AS", today=TODAY
        )

    # The TWR is +5%, then -2%: a cumulative 1.05 x 0.98 - 1 = 0.029 over Max.
    assert (result.base_date, result.end_date, result.rf_available) == (D1, D3, True)
    windows = {w.window.label: w for w in result.windows}
    assert list(windows) == ["1Y", "3Y", "5Y", "10Y", "Max"]
    assert windows["Max"].covered
    assert windows["Max"].returns.cumulative_return.value == pytest.approx(0.029)
    assert windows["Max"].drawdown.max_drawdown.reason == "insufficient history (have 2, need 200)"
    assert not windows["1Y"].covered
    assert result.excluded == []
    assert result.benchmark == BenchmarkStatus("IWDA.AS", True, None, D1)
    assert windows["Max"].relative.beta.reason == "insufficient history (have 0, need 104)"
    era, dip = (entry.row for entry in result.regimes.entries)
    assert (era.measured.start, era.measured.end, era.partial, era.ongoing) == (D1, D3, True, True)
    assert era.portfolio.cumulative_return.value == pytest.approx(0.029)
    assert dip.portfolio.cumulative_return.value == pytest.approx(-0.02)
    assert dip.benchmark.max_drawdown.value == pytest.approx(-0.02)


def test_the_default_benchmark_without_prices_is_reported_not_fatal(pg_connect):
    owner = _seed()

    with pg_connect() as conn:
        result = flows.compute_ex_post(conn, StubDataStoreClient(), user_id=owner, today=TODAY)

    assert result.benchmark == BenchmarkStatus("VWCE.DE", False, "no history yet", None)
    windows = {w.window.label: w for w in result.windows}
    assert windows["Max"].returns.cumulative_return.value == pytest.approx(0.029)
    assert windows["Max"].relative.beta.reason == "benchmark unavailable: no history yet"


@pytest.fixture
def client():
    settings = Settings(
        database_url=os.environ["PORTFOLIO_DATABASE_URL"],
        admin_database_url=os.environ["APP_POSTGRES_ADMIN_DATABASE_URL"],
        datastore_api_url="http://stub-datastore",
        internal_api_secret="stub-secret",  # pragma: allowlist secret
        api_internal_secret=_SECRET,
        api_host="0.0.0.0",  # noqa
        api_port=8002,
    )
    return TestClient(create_app(settings))


def test_get_risk_ex_post_serves_every_window(client):
    owner = _seed()

    response = client.get(
        "/risk/ex-post",
        params={"user_id": str(owner), "benchmark": "IWDA.AS"},
        headers={"X-Internal-Secret": _SECRET},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "ex-post · invested assets"
    assert (body["base_date"], body["end_date"]) == ("2026-01-05", "2026-01-07")
    assert [w["label"] for w in body["windows"]] == ["1Y", "3Y", "5Y", "10Y", "Max"]
    max_window = body["windows"][-1]
    assert max_window["cumulative_return"]["value"] == pytest.approx(0.029)
    assert max_window["cumulative_return"]["frequency"] == "daily"
    assert max_window["volatility"]["frequency"] == "weekly"
    assert max_window["sharpe"]["reason"] == "insufficient history (have 0, need 52)"
    assert body["windows"][0]["cagr"] == {
        "value": None,
        "window_start": "2025-01-07",
        "window_end": "2026-01-07",
        "frequency": "daily",
        "q": 0.0,
        "n_obs": 0,
        "n_gaps": 0,
        "flags": ["insufficient_history"],
        "reason": "insufficient history (portfolio history from 2026-01-05)",
        "standard_error": None,
    }
    assert (body["n_excluded"], body["excluded"]) == (0, [])
    assert body["benchmark"] == {
        "ticker": "IWDA.AS",
        "available": True,
        "reason": None,
        "first_date": "2026-01-05",
    }
    assert max_window["relative"]["beta"]["reason"] == "insufficient history (have 0, need 104)"
    assert max_window["relative"]["tracking_error"]["frequency"] == "weekly"
    # Two days of history: no full year, so no rolling point (RK-24).
    assert body["rolling"] == {"window": "1Y", "step": "monthly", "volatility": [], "sharpe": []}
    regimes = body["regimes"]
    assert (regimes["available"], regimes["total_loaded"]) == (True, 94)
    assert [(r["regime_id"], r["measured_start"], r["partial"]) for r in regimes["rows"]] == [
        ("era", "2026-01-05", True),
        ("dip", "2026-01-06", False),
    ]
    assert regimes["rows"][1]["portfolio"]["max_drawdown"]["flags"] == ["short_regime"]


def test_get_risk_ex_post_for_a_user_with_no_accounts_has_no_windows(client):
    _seed()

    response = client.get(
        "/risk/ex-post",
        params={"user_id": str(uuid.uuid4())},
        headers={"X-Internal-Secret": _SECRET},
    )

    assert response.status_code == 200
    assert response.json() == {
        "label": "ex-post · invested assets",
        "base_date": None,
        "end_date": None,
        "rf_available": False,
        "benchmark": {
            "ticker": "VWCE.DE",
            "available": False,
            "reason": "not fetched: nothing to measure",
            "first_date": None,
        },
        "windows": [],
        "n_excluded": 0,
        "excluded": [],
        "rolling": None,
        "regimes": None,
    }


def test_get_risk_ex_post_needs_the_internal_secret(client):
    response = client.get("/risk/ex-post", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 401
