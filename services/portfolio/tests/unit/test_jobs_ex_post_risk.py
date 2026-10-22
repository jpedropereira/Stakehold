"""risk_jobs.get_ex_post_risk: builds the data-store client, runs risk/flows.py on one connection,
closes it either way, logs its start and summary, and returns plain builtins the API's
ExPostRiskResponse accepts. Kept out of test_jobs.py, which is near the 64KB limit, so its
small helpers are repeated here rather than shared."""

import logging
import uuid
from datetime import date

import pytest
from portfolio import risk_jobs
from portfolio.api.models import ExPostRiskResponse
from portfolio.benchmark.base import BenchmarkStatus
from portfolio.risk.ex_post import WindowFigures
from portfolio.risk.flows import LABEL, ExPostRisk
from portfolio.risk.relative import RelativeFigures, relative_greyed
from portfolio.risk.single_series import (
    DrawdownFigures,
    ReturnFigures,
    TailFigures,
    WeeklyFigures,
)
from portfolio.risk.types import Flag, Frequency, RiskFigure, Window
from portfolio.settings import SettingsError
from portfolio.twr.flows import ExcludedHolding

BASE = date(2024, 1, 5)
VWCE = BenchmarkStatus("VWCE.DE", True, None, date(2019, 7, 29))
NOT_FETCHED = BenchmarkStatus("VWCE.DE", False, "not fetched: nothing to measure", None)
END = date(2026, 1, 9)
ONE_YEAR = Window("1Y", date(2025, 1, 9), END)
THREE_YEARS = Window("3Y", date(2023, 1, 9), END)


class _FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


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


def _figure(window, value, frequency=Frequency.DAILY, flags=(), reason=None, se=None):
    return RiskFigure(
        value=value,
        window_start=window.start,
        window_end=window.end,
        frequency=frequency,
        q=52.0 if frequency is Frequency.WEEKLY else 251.3,
        n_obs=0 if value is None and reason and "portfolio" in reason else 260,
        n_gaps=0,
        flags=frozenset(flags),
        reason=reason,
        standard_error=se,
    )


def _window(window, covered=True):
    if covered:
        daily = _figure(window, 0.1, flags={Flag.SHORT_WINDOW, Flag.NONSYNC_DAILY})
        weekly = _figure(window, 0.2, Frequency.WEEKLY)
        sharpe = _figure(window, 0.5, Frequency.WEEKLY, se=0.9)
        var_99 = _figure(window, None, reason="insufficient history (have 260, need 500)")
        drawdown = DrawdownFigures(daily, BASE, END, None, 30, ((BASE, 0.0), (END, -0.1)))
        tail = TailFigures(daily, daily, var_99, var_99)
        weekly_figures = WeeklyFigures(weekly, weekly, sharpe, weekly)
        beta = _figure(
            window,
            None,
            Frequency.WEEKLY,
            flags={Flag.INSUFFICIENT_HISTORY},
            reason="insufficient history (have 52, need 104)",
        )
        relative = RelativeFigures(beta, beta, weekly, weekly)
    else:
        reason = "insufficient history (portfolio history from 2024-01-05)"
        daily = _figure(window, None, reason=reason, flags={Flag.INSUFFICIENT_HISTORY})
        weekly = _figure(
            window, None, Frequency.WEEKLY, reason=reason, flags={Flag.INSUFFICIENT_HISTORY}
        )
        drawdown = DrawdownFigures(daily, None, None, None, None, ())
        tail = TailFigures(daily, daily, daily, daily)
        weekly_figures = WeeklyFigures(weekly, weekly, weekly, weekly)
        relative = relative_greyed(window, reason)
    return WindowFigures(
        window=window,
        covered=covered,
        returns=ReturnFigures(daily, daily),
        weekly=weekly_figures,
        drawdown=drawdown,
        calmar=daily,
        tail=tail,
        relative=relative,
    )


_RISK = ExPostRisk(
    label=LABEL,
    base_date=BASE,
    end_date=END,
    rf_available=True,
    benchmark=VWCE,
    windows=(_window(ONE_YEAR), _window(THREE_YEARS, covered=False)),
    excluded=[ExcludedHolding("U", "no_fx_rate", "no FX series for HKD")],
)


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(risk_jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(risk_jobs, "get_connection", lambda url: conn)
    return conn


def test_get_ex_post_risk_returns_builtins_the_response_model_accepts(monkeypatch, fake_conn):
    captured = {}

    def fake_compute(conn, client, **kw):
        captured.update(kw, conn=conn, client=client)
        return _RISK

    monkeypatch.setattr(risk_jobs.risk_flows, "compute_ex_post", fake_compute)
    user_id = uuid.uuid4()

    result = risk_jobs.get_ex_post_risk(user_id=user_id)

    assert result["label"] == "ex-post · invested assets"
    assert (result["base_date"], result["end_date"], result["rf_available"]) == (BASE, END, True)
    assert result["n_excluded"] == 1
    assert result["excluded"] == [
        {"ticker": "U", "status": "no_fx_rate", "reason": "no FX series for HKD"}
    ]
    one_year, three_years = result["windows"]
    assert {k: one_year[k] for k in ("label", "start", "end", "covered")} == {
        "label": "1Y",
        "start": ONE_YEAR.start,
        "end": END,
        "covered": True,
    }
    assert {k: one_year[k] for k in ("peak_date", "trough_date", "recovery_date")} == {
        "peak_date": BASE,
        "trough_date": END,
        "recovery_date": None,
    }
    assert one_year["max_days_under_water"] == 30
    assert "series" not in one_year  # the DD series isn't returned (agreed for RK-11)
    assert one_year["cagr"] == {
        "value": 0.1,
        "window_start": ONE_YEAR.start,
        "window_end": END,
        "frequency": "daily",
        "q": 251.3,
        "n_obs": 260,
        "n_gaps": 0,
        "flags": ["nonsync_daily", "short_window"],
        "reason": None,
        "standard_error": None,
    }
    assert one_year["sharpe"]["standard_error"] == 0.9
    assert one_year["sharpe"]["frequency"] == "weekly"
    assert one_year["var_99"]["reason"] == "insufficient history (have 260, need 500)"
    assert three_years["covered"] is False
    assert three_years["volatility"]["flags"] == ["insufficient_history"]
    # RK-13: the benchmark, and each window's figures against it.
    assert result["benchmark"] == {
        "ticker": "VWCE.DE",
        "available": True,
        "reason": None,
        "first_date": date(2019, 7, 29),
    }
    assert list(one_year["relative"]) == [
        "beta",
        "correlation",
        "tracking_error",
        "information_ratio",
    ]
    assert one_year["relative"]["beta"]["reason"] == "insufficient history (have 52, need 104)"
    assert one_year["relative"]["beta"]["flags"] == ["insufficient_history"]
    assert one_year["relative"]["tracking_error"]["frequency"] == "weekly"
    assert three_years["relative"]["beta"]["reason"] == (
        "insufficient history (portfolio history from 2024-01-05)"
    )
    ExPostRiskResponse.model_validate(result)
    assert captured["conn"] is fake_conn
    assert captured["user_id"] == user_id
    assert captured["benchmark"] == "VWCE.DE"
    assert captured["client"] is not None
    assert fake_conn.closed is True


def test_get_ex_post_risk_closes_the_connection_on_error(monkeypatch, fake_conn):
    def boom(conn, client, **kw):
        raise RuntimeError("db went away")

    monkeypatch.setattr(risk_jobs.risk_flows, "compute_ex_post", boom)

    with pytest.raises(RuntimeError):
        risk_jobs.get_ex_post_risk(user_id=uuid.uuid4())
    assert fake_conn.closed is True


def test_get_ex_post_risk_logs_a_start_line_and_a_summary(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(risk_jobs.risk_flows, "compute_ex_post", lambda c, d, **kw: _RISK)
    user_id = uuid.uuid4()

    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        risk_jobs.get_ex_post_risk(user_id=user_id)

    assert f"get-ex-post-risk: measuring ex-post risk for user {user_id}." in caplog.text
    assert (
        "get-ex-post-risk: done, 2024-01-05 to 2026-01-09: 1 window(s) measured, 1 greyed for "
        "short history, risk-free rate available, benchmark VWCE.DE (from 2019-07-29), 1 "
        "holding(s) left out." in caplog.text
    )


def test_get_ex_post_risk_passes_the_benchmark_and_says_when_it_is_unavailable(
    monkeypatch, fake_conn, caplog
):
    captured = {}
    unavailable = ExPostRisk(
        **{
            **_RISK.__dict__,
            "benchmark": BenchmarkStatus("NOPE", False, "not tracked", None),
        }
    )

    def fake_compute(conn, client, **kw):
        captured.update(kw)
        return unavailable

    monkeypatch.setattr(risk_jobs.risk_flows, "compute_ex_post", fake_compute)

    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        result = risk_jobs.get_ex_post_risk(user_id=uuid.uuid4(), benchmark="NOPE")

    assert captured["benchmark"] == "NOPE"
    assert result["benchmark"]["available"] is False
    assert "benchmark NOPE unavailable (not tracked)" in caplog.text


def test_get_ex_post_risk_says_when_the_risk_free_rate_was_unavailable(
    monkeypatch, fake_conn, caplog
):
    no_rf = ExPostRisk(LABEL, BASE, END, False, VWCE, (_window(ONE_YEAR),), [])
    monkeypatch.setattr(risk_jobs.risk_flows, "compute_ex_post", lambda c, d, **kw: no_rf)

    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        risk_jobs.get_ex_post_risk(user_id=uuid.uuid4())

    assert (
        "get-ex-post-risk: done, 2024-01-05 to 2026-01-09: 1 window(s) measured, 0 greyed for "
        "short history, risk-free rate unavailable, benchmark VWCE.DE (from 2019-07-29), 0 "
        "holding(s) left out." in caplog.text
    )


def test_get_ex_post_risk_says_plainly_when_there_is_nothing_to_measure(
    monkeypatch, fake_conn, caplog
):
    empty = ExPostRisk(LABEL, None, None, False, NOT_FETCHED, (), [])
    monkeypatch.setattr(risk_jobs.risk_flows, "compute_ex_post", lambda c, d, **kw: empty)

    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        result = risk_jobs.get_ex_post_risk(user_id=uuid.uuid4())

    assert result["windows"] == []
    ExPostRiskResponse.model_validate(result)
    assert (
        "get-ex-post-risk: done, no TWR returns in any account yet, so nothing to measure."
        in caplog.text
    )


def test_get_ex_post_risk_warns_when_holdings_exist_but_none_could_be_measured(
    monkeypatch, fake_conn, caplog
):
    none_left = ExPostRisk(
        LABEL,
        None,
        None,
        False,
        NOT_FETCHED,
        (),
        [ExcludedHolding("U", "failed", "couldn't reach data-store")],
    )
    monkeypatch.setattr(risk_jobs.risk_flows, "compute_ex_post", lambda c, d, **kw: none_left)

    with caplog.at_level(logging.INFO, logger="portfolio.risk_jobs"):
        risk_jobs.get_ex_post_risk(user_id=uuid.uuid4())

    warning = next(r for r in caplog.records if "get-ex-post-risk: done" in r.getMessage())
    assert warning.levelno == logging.WARNING
    assert warning.getMessage() == (
        "get-ex-post-risk: done, nothing to measure: 1 holding(s) left out, and the rest gave "
        "no TWR returns."
    )


def test_get_ex_post_risk_needs_data_store_settings(monkeypatch):
    monkeypatch.setattr(risk_jobs, "load_settings", lambda: _settings(datastore_api_url=None))

    with pytest.raises(SettingsError, match="ex-post risk"):
        risk_jobs.get_ex_post_risk(user_id=uuid.uuid4())
