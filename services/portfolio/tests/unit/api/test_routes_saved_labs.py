"""GET, POST and DELETE /labs/saved (LF-1). Routes stub saved_lab_jobs; a domain error reaches
the response through app.py's handler, so those cases build the full app."""

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from portfolio import saved_lab_jobs
from portfolio.api.app import create_app
from portfolio.errors import InvalidSavedLab, SavedLabNameTaken, SavedLabNotFound

USER = uuid.uuid4()
SECRET = "test-secret"  # pragma: allowlist secret


@pytest.fixture
def app_client(client):
    """The full app, with its domain-error handler, and the internal secret it checks."""
    settings = replace(client.app.state.settings, api_internal_secret=SECRET)
    return TestClient(create_app(settings), headers={"X-Internal-Secret": SECRET})


def _saved(**overrides):
    result = {"status": "ok", "rows_saved": 2, "labs_created": 1, "labs_replaced": 0}
    result["name"] = "Core"
    result.update(overrides)
    return result


def test_list_returns_the_jobs_labs(monkeypatch, client):
    captured = {}

    def fake_list(**kw):
        captured.update(kw)
        return {
            "status": "ok",
            "rows_saved": 0,
            "labs_listed": 1,
            "labs": [
                {
                    "name": "Core",
                    "assets": [{"ticker": "VWCE.DE", "weight": "60"}],
                    "created_at": "2026-11-01T09:00:00+00:00",
                    "updated_at": "2026-11-02T09:00:00+00:00",
                }
            ],
        }

    monkeypatch.setattr(saved_lab_jobs, "run_list_saved_labs", fake_list)

    response = client.get("/labs/saved", params={"user_id": str(USER)})

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "Core",
            "assets": [{"ticker": "VWCE.DE", "weight": "60"}],
            "created_at": "2026-11-01T09:00:00Z",
            "updated_at": "2026-11-02T09:00:00Z",
        }
    ]
    assert captured["user_id"] == USER


def test_list_requires_a_user_id(client):
    assert client.get("/labs/saved").status_code == 422


def test_save_answers_201_for_a_new_lab_and_passes_the_body_through(monkeypatch, client):
    captured = {}

    def fake_save(**kw):
        captured.update(kw)
        return _saved()

    monkeypatch.setattr(saved_lab_jobs, "run_save_lab", fake_save)

    response = client.post(
        "/labs/saved",
        json={
            "user_id": str(USER),
            "name": "Core",
            "assets": [{"ticker": "VWCE.DE", "weight": "60"}, {"ticker": "AAPL", "weight": "40"}],
        },
    )

    assert response.status_code == 201
    assert response.json() == {"status": "ok", "name": "Core", "created": True, "assets_saved": 2}
    assert captured["user_id"] == USER
    assert captured["name"] == "Core"
    assert captured["assets"] == [("VWCE.DE", Decimal("60")), ("AAPL", Decimal("40"))]
    assert captured["replace"] is False


def test_save_answers_200_for_a_replaced_lab(monkeypatch, client):
    captured = {}

    def fake_save(**kw):
        captured.update(kw)
        return _saved(labs_created=0, labs_replaced=1)

    monkeypatch.setattr(saved_lab_jobs, "run_save_lab", fake_save)

    response = client.post(
        "/labs/saved",
        json={
            "user_id": str(USER),
            "name": "Core",
            "assets": [{"ticker": "MSFT", "weight": "100"}],
            "replace": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["created"] is False
    assert captured["replace"] is True


@pytest.mark.parametrize(
    "error, status",
    [
        (SavedLabNameTaken("You already have a lab called 'Core'."), 409),
        (InvalidSavedLab("Enter a name for the lab."), 422),
    ],
)
def test_save_maps_domain_errors_to_their_status_with_the_message(
    monkeypatch, app_client, error, status
):
    def fake_save(**kw):
        raise error

    monkeypatch.setattr(saved_lab_jobs, "run_save_lab", fake_save)

    response = app_client.post(
        "/labs/saved",
        json={"user_id": str(USER), "name": "Core", "assets": [{"ticker": "A", "weight": "1"}]},
    )

    assert response.status_code == status
    assert response.json() == {"detail": str(error)}


def test_delete_passes_the_name_through(monkeypatch, client):
    captured = {}

    def fake_delete(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 0, "labs_deleted": 1}

    monkeypatch.setattr(saved_lab_jobs, "run_delete_saved_lab", fake_delete)

    response = client.delete("/labs/saved", params={"user_id": str(USER), "name": "Core & co"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "labs_deleted": 1}
    assert captured["user_id"] == USER
    assert captured["name"] == "Core & co"


def test_delete_answers_404_for_an_unknown_lab(monkeypatch, app_client):
    def fake_delete(**kw):
        raise SavedLabNotFound("You have no lab called 'Core'.")

    monkeypatch.setattr(saved_lab_jobs, "run_delete_saved_lab", fake_delete)

    response = app_client.delete("/labs/saved", params={"user_id": str(USER), "name": "Core"})

    assert response.status_code == 404
    assert response.json() == {"detail": "You have no lab called 'Core'."}
