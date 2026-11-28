"""saved_lab_jobs: each job opens and closes its own connection, calls one saved_labs flow, logs
its start and outcome, and returns builtins. Domain errors propagate, as in jobs.py."""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio import saved_lab_jobs
from portfolio.errors import SavedLabNameTaken, SavedLabNotFound
from portfolio.saved_labs.base import LabAsset, SavedLab
from portfolio.saved_labs.flows import SaveOutcome

USER = uuid.uuid4()
CREATED = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
UPDATED = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class _FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _settings():
    return type("S", (), {"database_url": "postgresql://fake/fake"})()  # pragma: allowlist secret


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(saved_lab_jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(saved_lab_jobs, "get_connection", lambda url: conn)
    return conn


def test_list_returns_each_lab_with_weights_as_plain_text(monkeypatch, fake_conn, caplog):
    labs = [
        SavedLab(
            name="Core",
            assets=(
                LabAsset("VWCE.DE", Decimal("60.000000")),
                LabAsset("AAPL", Decimal("33.333300")),
            ),
            created_at=CREATED,
            updated_at=UPDATED,
        )
    ]
    monkeypatch.setattr(saved_lab_jobs.flows, "list_labs", lambda conn, **kw: labs)

    with caplog.at_level(logging.INFO, logger="portfolio.saved_lab_jobs"):
        result = saved_lab_jobs.run_list_saved_labs(user_id=USER)

    assert result["status"] == "ok"
    assert result["labs_listed"] == 1
    assert result["labs"] == [
        {
            "name": "Core",
            "assets": [
                {"ticker": "VWCE.DE", "weight": "60"},
                {"ticker": "AAPL", "weight": "33.3333"},
            ],
            "created_at": CREATED.isoformat(),
            "updated_at": UPDATED.isoformat(),
        }
    ]
    assert fake_conn.closed
    assert f"saved labs list: listing labs for user {USER}." in caplog.text
    assert "saved labs list: done, 1 lab(s) found." in caplog.text


def test_list_with_no_labs_is_nothing_to_do(monkeypatch, fake_conn):
    monkeypatch.setattr(saved_lab_jobs.flows, "list_labs", lambda conn, **kw: [])

    result = saved_lab_jobs.run_list_saved_labs(user_id=USER)

    assert result["status"] == "nothing_to_do"
    assert result["labs"] == []


def test_save_passes_the_assets_through_and_reports_a_new_lab(monkeypatch, fake_conn, caplog):
    captured = {}

    def fake_save(conn, **kw):
        captured.update(kw)
        return SaveOutcome(name="Core", created=True, assets=2)

    monkeypatch.setattr(saved_lab_jobs.flows, "save_lab", fake_save)

    with caplog.at_level(logging.INFO, logger="portfolio.saved_lab_jobs"):
        result = saved_lab_jobs.run_save_lab(
            user_id=USER,
            name=" Core ",
            assets=[("VWCE.DE", Decimal("60")), ("AAPL", Decimal("40"))],
            replace=False,
        )

    assert captured == {
        "user_id": USER,
        "name": " Core ",
        "assets": [LabAsset("VWCE.DE", Decimal("60")), LabAsset("AAPL", Decimal("40"))],
        "replace": False,
    }
    assert result == {
        "status": "ok",
        "rows_saved": 2,
        "labs_created": 1,
        "labs_replaced": 0,
        "name": "Core",
    }
    assert fake_conn.closed
    assert "save lab: saving ' Core ' with 2 asset(s) for user" in caplog.text
    assert "save lab: done, lab 'Core' created with 2 asset(s)." in caplog.text


def test_save_reports_a_replaced_lab(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(
        saved_lab_jobs.flows,
        "save_lab",
        lambda conn, **kw: SaveOutcome(name="Core", created=False, assets=1),
    )

    with caplog.at_level(logging.INFO, logger="portfolio.saved_lab_jobs"):
        result = saved_lab_jobs.run_save_lab(
            user_id=USER, name="Core", assets=[("MSFT", Decimal("100"))], replace=True
        )

    assert (result["labs_created"], result["labs_replaced"]) == (0, 1)
    assert "save lab: done, lab 'Core' replaced with 1 asset(s)." in caplog.text


def test_save_lets_a_taken_name_propagate_and_still_closes(monkeypatch, fake_conn):
    def fake_save(conn, **kw):
        raise SavedLabNameTaken("You already have a lab called 'Core'.")

    monkeypatch.setattr(saved_lab_jobs.flows, "save_lab", fake_save)

    with pytest.raises(SavedLabNameTaken):
        saved_lab_jobs.run_save_lab(
            user_id=USER, name="Core", assets=[("A", Decimal("1"))], replace=False
        )

    assert fake_conn.closed


def test_delete_reports_one_lab_deleted(monkeypatch, fake_conn, caplog):
    captured = {}
    monkeypatch.setattr(saved_lab_jobs.flows, "delete_lab", lambda conn, **kw: captured.update(kw))

    with caplog.at_level(logging.INFO, logger="portfolio.saved_lab_jobs"):
        result = saved_lab_jobs.run_delete_saved_lab(user_id=USER, name="Core")

    assert captured == {"user_id": USER, "name": "Core"}
    assert result == {"status": "ok", "rows_saved": 0, "labs_deleted": 1}
    assert fake_conn.closed
    assert "delete lab: done, lab 'Core' deleted." in caplog.text


def test_delete_lets_not_found_propagate_and_still_closes(monkeypatch, fake_conn):
    def fake_delete(conn, **kw):
        raise SavedLabNotFound("You have no lab called 'Core'.")

    monkeypatch.setattr(saved_lab_jobs.flows, "delete_lab", fake_delete)

    with pytest.raises(SavedLabNotFound):
        saved_lab_jobs.run_delete_saved_lab(user_id=USER, name="Core")

    assert fake_conn.closed
