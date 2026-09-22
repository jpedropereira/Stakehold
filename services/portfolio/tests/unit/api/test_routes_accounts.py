import uuid

from portfolio import jobs


def test_list_accounts_returns_the_jobs_own_list(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_list_accounts",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 0,
            "accounts_listed": 1,
            "accounts": [
                {
                    "id": 1,
                    "account_label": "main",
                    "broker": "acme",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        },
    )

    response = client.get("/accounts", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "account_label": "main",
            "broker": "acme",
            "created_at": "2026-01-01T00:00:00Z",
        }
    ]


def test_list_accounts_passes_user_id_through(monkeypatch, client):
    captured = {}

    def fake_run_list_accounts(**kw):
        captured.update(kw)
        return {"status": "nothing_to_do", "rows_saved": 0, "accounts_listed": 0, "accounts": []}

    monkeypatch.setattr(jobs, "run_list_accounts", fake_run_list_accounts)
    user_id = uuid.uuid4()

    client.get("/accounts", params={"user_id": str(user_id)})

    assert captured["user_id"] == user_id
    assert captured["settings"] is not None


def test_list_accounts_requires_user_id(client):
    response = client.get("/accounts")

    assert response.status_code == 422


def test_create_account_returns_201_when_created(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_add_account",
        lambda **kw: {"status": "ok", "rows_saved": 1, "accounts_created": 1},
    )

    response = client.post(
        "/accounts",
        json={"user_id": str(uuid.uuid4()), "broker": "acme", "account_label": "main"},
    )

    assert response.status_code == 201
    assert response.json() == {"status": "ok", "rows_saved": 1, "accounts_created": 1}


def test_create_account_returns_200_when_nothing_to_do(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_add_account",
        lambda **kw: {"status": "nothing_to_do", "rows_saved": 0, "accounts_created": 0},
    )

    response = client.post(
        "/accounts",
        json={"user_id": str(uuid.uuid4()), "broker": "acme", "account_label": "main"},
    )

    assert response.status_code == 200


def test_create_account_passes_fields_through(monkeypatch, client):
    captured = {}

    def fake_run_add_account(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 1, "accounts_created": 1}

    monkeypatch.setattr(jobs, "run_add_account", fake_run_add_account)
    user_id = uuid.uuid4()

    client.post(
        "/accounts", json={"user_id": str(user_id), "broker": "acme", "account_label": "main"}
    )

    assert captured["user_id"] == user_id
    assert captured["broker"] == "acme"
    assert captured["account_label"] == "main"
    assert captured["settings"] is not None


def test_create_account_rejects_a_malformed_body(client):
    response = client.post("/accounts", json={"broker": "acme", "account_label": "main"})

    assert response.status_code == 422


def test_list_brokers_returns_the_jobs_own_list(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_list_available_brokers",
        lambda: {"status": "ok", "rows_saved": 0, "brokers_available": 2, "brokers": ["a", "b"]},
    )

    response = client.get("/brokers")

    assert response.status_code == 200
    assert response.json() == ["a", "b"]


def test_list_unmapped_instruments_returns_the_jobs_own_list(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_list_unmapped_instruments",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 0,
            "instruments_unmapped": 1,
            "unmapped_instruments": [
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
            ],
        },
    )

    response = client.get("/instruments/unmapped", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json() == [{"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}]
