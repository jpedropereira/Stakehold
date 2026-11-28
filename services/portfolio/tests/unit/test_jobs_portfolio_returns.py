"""jobs.get_portfolio_returns: loads the user's cross-account return series on one connection,
closes it either way, and logs its start and outcome. Kept out of test_jobs.py, which is near the
64KB limit, so its small helpers are repeated here rather than shared."""

import logging
import uuid
from datetime import date
from decimal import Decimal

import pytest
from portfolio import jobs
from portfolio.holdings.flows import PortfolioReturns
from portfolio.returns.performance import PortfolioPoint


class _FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _settings():
    return type(
        "S",
        (),
        {
            "database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "admin_database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "datastore_api_url": None,
            "internal_api_secret": None,
        },
    )()


def _point(on):
    return PortfolioPoint(
        price_date=on,
        market_value_eur=Decimal("1100"),
        cost_basis_eur=Decimal("1000"),
        dividends_received_eur=Decimal("0"),
        total_return_eur=Decimal("100"),
        total_return_pct=Decimal("0.10000000"),
    )


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: conn)
    monkeypatch.setattr(jobs, "user_connection", lambda settings, user_id: conn)
    monkeypatch.setattr(jobs, "cross_user_connection", lambda settings: conn)
    return conn


def test_get_portfolio_returns_returns_a_dict_and_closes_connection(monkeypatch, fake_conn):
    captured = {}

    def fake_list(conn, **kw):
        captured.update(kw, conn=conn)
        return PortfolioReturns(series=[_point(date(2026, 1, 6))])

    monkeypatch.setattr(jobs.holdings_flows, "list_portfolio_returns", fake_list)
    user_id = uuid.uuid4()

    result = jobs.get_portfolio_returns(
        user_id=user_id, start=date(2026, 1, 1), end=date(2026, 2, 1)
    )

    assert result == {
        "series": [
            {
                "price_date": date(2026, 1, 6),
                "market_value_eur": Decimal("1100"),
                "cost_basis_eur": Decimal("1000"),
                "dividends_received_eur": Decimal("0"),
                "total_return_eur": Decimal("100"),
                "total_return_pct": Decimal("0.10000000"),
            }
        ],
    }
    assert captured == {
        "conn": fake_conn,
        "user_id": user_id,
        "start": date(2026, 1, 1),
        "end": date(2026, 2, 1),
    }
    assert fake_conn.closed is True


def test_get_portfolio_returns_closes_the_connection_on_error(monkeypatch, fake_conn):
    def boom(conn, **kw):
        raise RuntimeError("db went away")

    monkeypatch.setattr(jobs.holdings_flows, "list_portfolio_returns", boom)

    with pytest.raises(RuntimeError):
        jobs.get_portfolio_returns(user_id=uuid.uuid4())
    assert fake_conn.closed is True


def test_get_portfolio_returns_logs_the_days_it_covers(monkeypatch, fake_conn, caplog):
    result = PortfolioReturns(series=[_point(date(2026, 1, 5)), _point(date(2026, 1, 6))])
    monkeypatch.setattr(jobs.holdings_flows, "list_portfolio_returns", lambda c, **kw: result)
    user_id = uuid.uuid4()

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_portfolio_returns(user_id=user_id)

    assert f"get-portfolio-returns: computing returns across accounts for user {user_id}" in (
        caplog.text
    )
    assert "get-portfolio-returns: done, 2 day(s), 2026-01-05 to 2026-01-06." in caplog.text


def test_get_portfolio_returns_says_plainly_when_there_is_nothing(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(
        jobs.holdings_flows,
        "list_portfolio_returns",
        lambda c, **kw: PortfolioReturns(series=[]),
    )

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        result = jobs.get_portfolio_returns(user_id=uuid.uuid4())

    assert result == {"series": []}
    assert (
        "get-portfolio-returns: done, no return rows in any account yet, so no series to "
        "report." in caplog.text
    )


def test_get_portfolio_returns_says_when_the_range_holds_no_days(monkeypatch, fake_conn, caplog):
    # With a range given, an empty series means nothing in it -- not necessarily no rows at all.
    monkeypatch.setattr(
        jobs.holdings_flows, "list_portfolio_returns", lambda c, **kw: PortfolioReturns(series=[])
    )

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_portfolio_returns(user_id=uuid.uuid4(), start=date(2030, 1, 1))

    assert "get-portfolio-returns: done, no days in the requested range." in caplog.text
