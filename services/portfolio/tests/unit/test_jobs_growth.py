"""jobs.get_growth: loads the ticker mappings and the account's growth on one connection, closes
it either way, and logs its start and outcome. Kept out of test_jobs.py, which is near the 64KB
limit, so its small helpers are repeated here rather than shared."""

import logging
import uuid
from datetime import date
from decimal import Decimal

import pytest
from portfolio import jobs
from portfolio.errors import AccountNotFound
from portfolio.returns.performance import GrowthPoint
from portfolio.ticker_mapping.base import TickerMappings


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


_MAPPINGS = TickerMappings(isin={"IE00B4L5Y983": "IWDA.AS"}, symbol={})


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: conn)
    monkeypatch.setattr(jobs.pipeline, "load_ticker_mappings", lambda c: _MAPPINGS)
    return conn


def test_get_growth_returns_dicts_and_closes_connection(monkeypatch, fake_conn):
    captured = {}

    def fake_list_growth(conn, **kw):
        captured.update(kw, conn=conn)
        return [GrowthPoint(price_date=date(2026, 1, 6), growth_pct=Decimal("0.10000000"))]

    monkeypatch.setattr(jobs.pipeline, "list_growth", fake_list_growth)
    user_id = uuid.uuid4()

    result = jobs.get_growth(user_id=user_id, account_label="m")

    assert result == [{"price_date": date(2026, 1, 6), "growth_pct": Decimal("0.10000000")}]
    assert captured == {
        "conn": fake_conn,
        "user_id": user_id,
        "account_label": "m",
        "mappings": _MAPPINGS,
    }
    assert fake_conn.closed is True


def test_get_growth_propagates_account_not_found_and_still_closes(monkeypatch, fake_conn):
    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "list_growth", boom)

    with pytest.raises(AccountNotFound):
        jobs.get_growth(user_id=uuid.uuid4(), account_label="nope")
    assert fake_conn.closed is True


def test_get_growth_logs_the_account_and_how_many_days_it_covers(monkeypatch, fake_conn, caplog):
    points = [
        GrowthPoint(price_date=date(2026, 1, 5), growth_pct=Decimal("0")),
        GrowthPoint(price_date=date(2026, 1, 6), growth_pct=Decimal("0.12340000")),
    ]
    monkeypatch.setattr(jobs.pipeline, "list_growth", lambda conn, **kw: points)

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_growth(user_id=uuid.uuid4(), account_label="isa")

    assert "get-growth: computing growth for account 'isa'" in caplog.text
    assert "get-growth: done, 2 day(s), 2026-01-05 to 2026-01-06." in caplog.text


def test_get_growth_says_plainly_when_there_is_nothing_to_compute(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(jobs.pipeline, "list_growth", lambda conn, **kw: [])

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        result = jobs.get_growth(user_id=uuid.uuid4(), account_label="m")

    assert result == []
    assert "get-growth: done, no return rows yet, so no growth to report." in caplog.text
