"""jobs.get_portfolio_twr: builds the data-store client, runs twr/flows.py on one connection,
closes it either way, logs its start and summary, and returns plain builtins the API's
PortfolioTwrResponse accepts. Kept out of test_jobs.py, which is near the 64KB limit, so its
small helpers are repeated here rather than shared."""

import logging
import uuid
from datetime import date
from decimal import Decimal

import pytest
from portfolio import jobs
from portfolio.api.models import PortfolioTwrResponse
from portfolio.settings import SettingsError
from portfolio.twr.calculator import TwrPoint
from portfolio.twr.flows import ExcludedHolding, PortfolioTwr

D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)


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


_TWR = PortfolioTwr(
    base_date=D1,
    points=[
        TwrPoint(D2, None, Decimal("1.00000000")),
        TwrPoint(D3, Decimal("0.01000000"), Decimal("1.01000000")),
    ],
    n_gaps=1,
    excluded=[ExcludedHolding("U", "no_fx_rate", "no FX series for HKD")],
)


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: conn)
    monkeypatch.setattr(jobs, "user_connection", lambda settings, user_id: conn)
    monkeypatch.setattr(jobs, "cross_user_connection", lambda settings: conn)
    return conn


def test_get_portfolio_twr_returns_builtins_the_response_model_accepts(monkeypatch, fake_conn):
    captured = {}

    def fake_compute(conn, client, **kw):
        captured.update(kw, conn=conn, client=client)
        return _TWR

    monkeypatch.setattr(jobs.twr_flows, "compute_portfolio_twr", fake_compute)
    user_id = uuid.uuid4()

    result = jobs.get_portfolio_twr(user_id=user_id)

    assert result == {
        "base_date": D1,
        "points": [
            {"price_date": D2, "twr_return": None, "twr_growth": Decimal("1.00000000")},
            {"price_date": D3, "twr_return": Decimal("0.01000000"), "twr_growth": Decimal("1.01")},
        ],
        "n_gaps": 1,
        "n_excluded": 1,
        "excluded": [{"ticker": "U", "status": "no_fx_rate", "reason": "no FX series for HKD"}],
    }
    PortfolioTwrResponse.model_validate(result)
    assert captured["conn"] is fake_conn
    assert captured["user_id"] == user_id
    assert captured["client"] is not None
    assert fake_conn.closed is True


def test_get_portfolio_twr_closes_the_connection_on_error(monkeypatch, fake_conn):
    def boom(conn, client, **kw):
        raise RuntimeError("db went away")

    monkeypatch.setattr(jobs.twr_flows, "compute_portfolio_twr", boom)

    with pytest.raises(RuntimeError):
        jobs.get_portfolio_twr(user_id=uuid.uuid4())
    assert fake_conn.closed is True


def test_get_portfolio_twr_logs_a_start_line_and_a_summary(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(jobs.twr_flows, "compute_portfolio_twr", lambda c, d, **kw: _TWR)
    user_id = uuid.uuid4()

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_portfolio_twr(user_id=user_id)

    assert f"get-portfolio-twr: computing the TWR across accounts for user {user_id}." in (
        caplog.text
    )
    assert (
        "get-portfolio-twr: done, 2 day(s) from 2026-01-06 to 2026-01-07, 1 gap(s), "
        "1 holding(s) left out." in caplog.text
    )


def test_get_portfolio_twr_says_plainly_when_there_are_no_rows(monkeypatch, fake_conn, caplog):
    empty = PortfolioTwr(base_date=None, points=[], n_gaps=0, excluded=[])
    monkeypatch.setattr(jobs.twr_flows, "compute_portfolio_twr", lambda c, d, **kw: empty)

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_portfolio_twr(user_id=uuid.uuid4())

    assert (
        "get-portfolio-twr: done, no return rows in any account yet, so no series to report."
        in caplog.text
    )


def test_get_portfolio_twr_warns_when_holdings_exist_but_no_return_could_be_computed(
    monkeypatch, fake_conn, caplog
):
    # Every holding left out: rows exist, so this isn't the "nothing yet" case.
    none_left = PortfolioTwr(
        base_date=None,
        points=[],
        n_gaps=0,
        excluded=[ExcludedHolding("U", "failed", "couldn't reach data-store")],
    )
    monkeypatch.setattr(jobs.twr_flows, "compute_portfolio_twr", lambda c, d, **kw: none_left)

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_portfolio_twr(user_id=uuid.uuid4())

    warning = next(r for r in caplog.records if "get-portfolio-twr: done" in r.getMessage())
    assert warning.levelno == logging.WARNING
    assert warning.getMessage() == (
        "get-portfolio-twr: done, no returns: 1 holding(s) left out, and the rest held nothing "
        "on two consecutive dates."
    )


def test_get_portfolio_twr_needs_data_store_settings(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", lambda: _settings(datastore_api_url=None))

    with pytest.raises(SettingsError, match="the portfolio TWR"):
        jobs.get_portfolio_twr(user_id=uuid.uuid4())
