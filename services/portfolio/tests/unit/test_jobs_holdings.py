"""jobs.get_holdings: reads every account's holdings on one connection, closes it either way, and
logs its start and outcome. Kept out of test_jobs.py, which is near the 64KB limit, so its small
helpers are repeated here rather than shared."""

import logging
import uuid
from datetime import date
from decimal import Decimal

import pytest
from portfolio import jobs
from portfolio.holdings.aggregator import (
    Holding,
    HoldingAccount,
    HoldingsResult,
    HoldingsTotals,
)


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


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: conn)
    return conn


def _entry(label, quantity):
    return HoldingAccount(
        account_label=label,
        quantity=Decimal(quantity),
        native_currency="EUR",
        average_cost_native=Decimal("80"),
        cost_basis_eur=Decimal("80") * Decimal(quantity),
        market_value_native=None,
        market_value_eur=None,
        unrealized_return_eur=None,
    )


_HOLDING = Holding(
    canonical_ticker="IWDA.AS",
    quantity=Decimal("140"),
    average_cost_eur=Decimal("80.000000"),
    cost_basis_eur=Decimal("11200"),
    native_currency="EUR",
    mixed_currency=False,
    average_cost_native=Decimal("80.000000"),
    market_value_native=None,
    priced=False,
    price_date=None,
    market_value_eur=None,
    unrealized_return_eur=None,
    unrealized_return_pct=None,
    dividends_received_eur=None,
    total_return_eur=None,
    total_return_pct=None,
    accounts=(_entry("A", "120"), _entry("B", "20")),
)

_TOTALS = HoldingsTotals(
    price_date=date(2026, 1, 6),
    market_value_eur=Decimal("30"),
    cost_basis_eur=Decimal("0"),
    dividends_received_eur=Decimal("30"),
    total_return_eur=Decimal("30"),
    total_return_pct=None,
)


def test_get_holdings_returns_a_dict_and_closes_the_connection(monkeypatch, fake_conn):
    captured = {}

    def fake_list_holdings(conn, **kw):
        captured.update(kw, conn=conn)
        return HoldingsResult(holdings=[_HOLDING], totals=_TOTALS)

    monkeypatch.setattr(jobs.holdings_flows, "list_holdings", fake_list_holdings)
    user_id = uuid.uuid4()

    result = jobs.get_holdings(user_id=user_id)

    assert captured == {"conn": fake_conn, "user_id": user_id}
    assert result["totals"]["dividends_received_eur"] == Decimal("30")
    assert result["holdings"][0]["quantity"] == Decimal("140")
    assert [e["account_label"] for e in result["holdings"][0]["accounts"]] == ["A", "B"]
    assert fake_conn.closed is True


def test_get_holdings_still_closes_the_connection_on_error(monkeypatch, fake_conn):
    def boom(conn, **kw):
        raise RuntimeError("db went away")

    monkeypatch.setattr(jobs.holdings_flows, "list_holdings", boom)

    with pytest.raises(RuntimeError):
        jobs.get_holdings(user_id=uuid.uuid4())
    assert fake_conn.closed is True


def test_get_holdings_logs_the_user_and_how_many_holdings_it_found(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(
        jobs.holdings_flows,
        "list_holdings",
        lambda conn, **kw: HoldingsResult(holdings=[_HOLDING], totals=_TOTALS),
    )
    user_id = uuid.uuid4()

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.get_holdings(user_id=user_id)

    assert f"get-holdings: listing holdings across accounts for user {user_id}." in caplog.text
    assert "get-holdings: done, 1 holding(s) across 2 account(s)." in caplog.text


def test_get_holdings_says_plainly_when_there_are_no_open_holdings(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(
        jobs.holdings_flows,
        "list_holdings",
        lambda conn, **kw: HoldingsResult(holdings=[], totals=None),
    )

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        result = jobs.get_holdings(user_id=uuid.uuid4())

    assert result == {"holdings": [], "totals": None}
    assert "get-holdings: done, no open holdings in any account." in caplog.text
