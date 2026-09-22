import uuid

from portfolio import jobs


def _position_dict(**overrides):
    values = dict(
        account_id=1,
        canonical_ticker="IWDA.AS",
        as_of_date="2026-01-05",
        quantity="10.000000",
        average_cost_native="95.000000",
        native_currency="EUR",
        average_cost_eur="95.000000",
        cost_basis_eur="950.0000",
    )
    values.update(overrides)
    return values


def test_list_positions_returns_the_jobs_own_list(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_positions", lambda **kw: [_position_dict()])

    response = client.get("/positions", params={"user_id": str(uuid.uuid4()), "account_label": "m"})

    assert response.status_code == 200
    assert response.json() == [_position_dict()]


def test_list_positions_requires_account_label(client):
    response = client.get("/positions", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 422


def test_list_positions_passes_user_and_account_through(monkeypatch, client):
    captured = {}

    def fake_get_positions(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(jobs, "get_positions", fake_get_positions)
    user_id = uuid.uuid4()

    client.get("/positions", params={"user_id": str(user_id), "account_label": "m"})

    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"
    assert captured["settings"] is not None


def test_rebuild_positions_returns_200_even_when_partial(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_rebuild_positions",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 1,
            "positions_rebuilt": 1,
            "positions_removed": 0,
            "instruments_blocked": 1,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
            ],
            "failed_pairs": [],
        },
    )

    response = client.post(
        "/positions/rebuild", json={"user_id": str(uuid.uuid4()), "account_label": "m"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["blocked_instruments"] == [
        {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
    ]


def test_rebuild_positions_without_account_label_rebuilds_every_account(monkeypatch, client):
    captured = {}

    def fake_run_rebuild_positions(**kw):
        captured.update(kw)
        return {
            "status": "ok",
            "rows_saved": 0,
            "positions_rebuilt": 0,
            "positions_removed": 0,
            "instruments_blocked": 0,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [],
            "failed_pairs": [],
        }

    monkeypatch.setattr(jobs, "run_rebuild_positions", fake_run_rebuild_positions)
    user_id = uuid.uuid4()

    client.post("/positions/rebuild", json={"user_id": str(user_id)})

    assert captured["user_id"] == user_id
    assert captured["account_label"] is None
    assert captured["settings"] is not None
