"""jobs.get_backtest: builds the data-store client, runs labs/flows.py, logs its start and summary,
and returns plain builtins the API's BacktestResponse accepts. Kept out of test_jobs.py, which is
near the 64KB limit, so its small helpers are repeated here rather than shared."""

import logging
from datetime import date
from decimal import Decimal

import pytest
from portfolio import jobs
from portfolio.api.models import BacktestResponse
from portfolio.datastore_client.base import (
    PricePoint,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
from portfolio.labs.calculator import Period
from portfolio.settings import SettingsError


def _settings(
    datastore_api_url="http://datastore:8000",
    internal_api_secret="secret",  # pragma: allowlist secret
):
    return type(
        "S",
        (),
        {
            "database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "admin_database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "datastore_api_url": datastore_api_url,
            "internal_api_secret": internal_api_secret,  # pragma: allowlist secret
        },
    )()


class _FakeDataStoreClient:
    """A and S are in EUR. A has a year of history, so it covers 1M to 1Y. S starts the day
    before the end, so it covers no month period. Anything else is untracked."""

    def get_ticker_info(self, ticker):
        if ticker in ("A", "S"):
            return TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
        return TickerInfo(exists=False, has_returns=False)

    def get_price_series(self, ticker, start, end):
        if ticker == "S":
            return [
                PricePoint(date(2026, 1, 6), Decimal("100"), None),
                PricePoint(date(2026, 1, 7), Decimal("100"), Decimal("0")),
            ]
        return [
            PricePoint(date(2025, 1, 2), Decimal("100"), None),
            PricePoint(date(2026, 1, 5), Decimal("100"), Decimal("0")),
            PricePoint(date(2026, 1, 6), Decimal("101"), Decimal("0.01")),
            PricePoint(date(2026, 1, 7), Decimal("99.99"), Decimal("-0.01")),
        ]

    def get_fx_rate_series(self, base, quote, start, end):
        raise AssertionError("a EUR-only mix needs no FX")

    def get_risk_free_series(self, start, end):
        point = RiskFreePoint(date(2024, 12, 31), Decimal("2.9000"), "estr")
        return RiskFreeSeries(first_date=date(1999, 1, 4), points=[point])


_client = _FakeDataStoreClient()


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _settings)
    monkeypatch.setattr(jobs, "_require_datastore_client", lambda settings, command: _client)
    return _client


def test_get_backtest_returns_builtins_the_response_model_accepts(fake_client):
    result = jobs.get_backtest(assets=[("A", Decimal("1")), ("X", Decimal("1"))], period=Period.MAX)

    response = BacktestResponse.model_validate(result)
    assert response.end_date == date(2026, 1, 7)
    assert [row.ticker for row in response.assets] == ["A"]
    assert [(s.ticker, s.status) for s in response.statuses] == [
        ("A", "included"),
        ("X", "not_tracked"),
    ]
    assert set(response.assets[0].periods) == {p.value for p in Period}
    assert response.risk.common_start == date(2025, 1, 2)
    assert [row.ticker for row in response.risk.assets] == ["A"]
    assert response.risk.mix.windows["Max"].covered is True
    # RK-13: the default benchmark, untracked here, greys the relative figures with its reason.
    assert response.risk.benchmark.ticker == "VWCE.DE"
    assert response.risk.benchmark.available is False
    beta = response.risk.mix.windows["Max"].relative.beta
    assert beta.reason == "benchmark unavailable: not tracked"


def test_get_backtest_measures_against_the_named_benchmark(fake_client):
    result = jobs.get_backtest(assets=[("A", Decimal("1"))], period=Period.MAX, benchmark="A")

    benchmark = BacktestResponse.model_validate(result).risk.benchmark
    assert (benchmark.ticker, benchmark.available, benchmark.first_date) == (
        "A",
        True,
        date(2025, 1, 2),
    )
    flags = result["risk"]["mix"]["windows"]["Max"]["relative"]["beta"]["flags"]
    assert isinstance(flags, list)


def test_get_backtest_sorts_each_figures_flags(fake_client):
    result = jobs.get_backtest(assets=[("A", Decimal("1"))], period=Period.MAX)

    flags = result["risk"]["mix"]["windows"]["Max"]["max_drawdown"]["flags"]
    assert flags == sorted(flags)
    assert isinstance(flags, list)


def test_get_backtest_logs_a_start_line_and_a_summary(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_backtest(assets=[("A", Decimal("1")), ("X", Decimal("1"))], period=Period.Y1)

    messages = [r.getMessage() for r in caplog.records]
    assert any("2 asset(s)" in m and "1Y" in m and "against VWCE.DE" in m for m in messages)
    assert any("1 included" in m and "1 left out" in m and "2026-01-07" in m for m in messages)


def test_get_backtest_logs_each_asset_the_mix_left_out_and_for_which_periods(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_backtest(assets=[("A", Decimal("1")), ("S", Decimal("1"))], period=Period.Y1)

    messages = [r.getMessage() for r in caplog.records]
    assert "backtest: the mix leaves out A for 2Y, 5Y: not enough history." in messages
    assert (
        "backtest: the mix leaves out S for 1M, 3M, 6M, 1Y, 2Y, 5Y: not enough history." in messages
    )


def test_get_backtest_says_so_when_no_asset_could_be_included(fake_client, caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        result = jobs.get_backtest(assets=[("X", Decimal("1"))], period=Period.MAX)

    assert result["end_date"] is None
    assert result["risk"] is None
    assert any("no asset could be included" in r.getMessage() for r in caplog.records)


def test_get_backtest_needs_data_store_settings(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", lambda: _settings(datastore_api_url=None))

    with pytest.raises(SettingsError, match="a backtest"):
        jobs.get_backtest(assets=[("A", Decimal("1"))], period=Period.MAX)
