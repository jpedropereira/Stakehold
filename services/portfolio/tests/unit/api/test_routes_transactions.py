import uuid

import pytest
from portfolio import jobs
from portfolio.errors import AccountNotFound


def test_list_transactions_returns_the_jobs_own_list(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_transactions",
        lambda **kw: [
            {
                "account_id": 1,
                "broker_transaction_ref": "ref-1",
                "transaction_type": "BUY",
                "trade_datetime": "2026-01-05T00:00:00+00:00",
                "instrument_identifier": "IE00B4L5Y983",
                "instrument_identifier_kind": "ISIN",
                "quantity": "10",
                "price": "95.00",
                "native_currency": "EUR",
                "native_amount": "-950.00",
                "eur_amount": "-950.0000",
                "fx_rate": None,
                "fx_rate_source": None,
                "fees_native": None,
            }
        ],
    )

    response = client.get(
        "/transactions", params={"user_id": str(uuid.uuid4()), "account_label": "m"}
    )

    assert response.status_code == 200
    assert response.json()[0]["broker_transaction_ref"] == "ref-1"
    assert response.json()[0]["quantity"] == "10"


def test_list_transactions_requires_account_label(client):
    response = client.get("/transactions", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 422


def test_create_transaction_returns_201_when_saved(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_add_transaction",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 1,
            "transactions_saved": 1,
            "positions_rebuild": "ok",
        },
    )

    response = client.post(
        "/transactions",
        json={
            "user_id": str(uuid.uuid4()),
            "account_label": "m",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "DEPOSIT",
            "trade_datetime": "2026-01-05T00:00:00+00:00",
            "native_amount": "100.00",
            "native_currency": "EUR",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "status": "ok",
        "rows_saved": 1,
        "transactions_saved": 1,
        "positions_rebuild": "ok",
    }


def test_create_transaction_returns_200_when_nothing_to_do(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_add_transaction",
        lambda **kw: {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "transactions_saved": 0,
            "positions_rebuild": "skipped",
        },
    )

    response = client.post(
        "/transactions",
        json={
            "user_id": str(uuid.uuid4()),
            "account_label": "m",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "DEPOSIT",
            "trade_datetime": "2026-01-05T00:00:00+00:00",
            "native_amount": "100.00",
            "native_currency": "EUR",
        },
    )

    assert response.status_code == 200


def test_create_transaction_rejects_an_unknown_transaction_type(client):
    response = client.post(
        "/transactions",
        json={
            "user_id": str(uuid.uuid4()),
            "account_label": "m",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "NOT_A_TYPE",
            "trade_datetime": "2026-01-05T00:00:00+00:00",
            "native_amount": "100.00",
            "native_currency": "EUR",
        },
    )

    assert response.status_code == 422


def test_create_transaction_rejects_a_naive_datetime(client):
    response = client.post(
        "/transactions",
        json={
            "user_id": str(uuid.uuid4()),
            "account_label": "m",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "DEPOSIT",
            "trade_datetime": "2026-01-05T00:00:00",
            "native_amount": "100.00",
            "native_currency": "EUR",
        },
    )

    assert response.status_code == 422


def test_create_transaction_passes_fields_through(monkeypatch, client):
    captured = {}

    def fake_run_add_transaction(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 1, "transactions_saved": 1, "positions_rebuild": "ok"}

    monkeypatch.setattr(jobs, "run_add_transaction", fake_run_add_transaction)
    user_id = uuid.uuid4()

    client.post(
        "/transactions",
        json={
            "user_id": str(user_id),
            "account_label": "m",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "DEPOSIT",
            "trade_datetime": "2026-01-05T00:00:00+00:00",
            "native_amount": "100.00",
            "native_currency": "EUR",
        },
    )

    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"
    assert captured["broker_transaction_ref"] == "ref-1"
    assert captured["transaction_type"] == "DEPOSIT"
    assert str(captured["native_amount"]) == "100.00"
    assert captured["native_currency"] == "EUR"


def test_import_transactions_streams_the_upload_to_a_temp_path(monkeypatch, client):
    captured = {}

    def fake_run_import_transactions(*, user_id, account_label, file_path, settings):
        captured["user_id"] = user_id
        captured["account_label"] = account_label
        captured["file_path"] = file_path
        captured["file_exists_during_call"] = file_path.exists()
        captured["file_contents"] = file_path.read_bytes()
        return {"status": "ok", "rows_saved": 3, "transactions_saved": 3, "positions_rebuild": "ok"}

    monkeypatch.setattr(jobs, "run_import_transactions", fake_run_import_transactions)
    user_id = uuid.uuid4()

    response = client.post(
        "/transactions/import",
        data={"user_id": str(user_id), "account_label": "m"},
        files={"file": ("export.csv", b"a,b,c\n1,2,3\n", "text/csv")},
    )

    assert response.status_code == 201
    assert response.json() == {
        "status": "ok",
        "rows_saved": 3,
        "transactions_saved": 3,
        "positions_rebuild": "ok",
    }
    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"
    assert captured["file_exists_during_call"] is True
    assert captured["file_contents"] == b"a,b,c\n1,2,3\n"
    # The temp file is deleted after the job returns, whether it succeeded or raised.
    assert not captured["file_path"].exists()


def test_import_transactions_deletes_the_temp_file_when_the_job_raises(monkeypatch, client):
    captured = {}

    def boom(*, user_id, account_label, file_path, settings):
        captured["file_path"] = file_path
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs, "run_import_transactions", boom)

    with pytest.raises(AccountNotFound):
        client.post(
            "/transactions/import",
            data={"user_id": str(uuid.uuid4()), "account_label": "nope"},
            files={"file": ("export.csv", b"data", "text/csv")},
        )

    assert not captured["file_path"].exists()


def test_import_cashflow_streams_the_upload_to_a_temp_path(monkeypatch, client):
    captured = {}

    def fake_run_import_cashflow(*, user_id, account_label, file_path, settings):
        captured["file_contents"] = file_path.read_bytes()
        return {"status": "ok", "rows_saved": 1, "transactions_saved": 1, "positions_rebuild": "ok"}

    monkeypatch.setattr(jobs, "run_import_cashflow", fake_run_import_cashflow)

    response = client.post(
        "/cashflows/import",
        data={"user_id": str(uuid.uuid4()), "account_label": "m"},
        files={"file": ("account.csv", b"cash,flow\n", "text/csv")},
    )

    assert response.status_code == 201
    assert captured["file_contents"] == b"cash,flow\n"


def test_list_tickers_returns_the_jobs_list(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_tickers", lambda **kw: ["AAPL", "IWDA.AS"])

    response = client.get("/instruments/tickers")

    assert response.status_code == 200
    assert response.json() == ["AAPL", "IWDA.AS"]


def test_create_transaction_accepts_a_buy_without_an_amount(monkeypatch, client):
    captured = {}

    def fake_add(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 1, "transactions_saved": 1, "positions_rebuild": "ok"}

    monkeypatch.setattr(jobs, "run_add_transaction", fake_add)

    response = client.post(
        "/transactions",
        json={
            "user_id": str(uuid.uuid4()),
            "account_label": "m",
            "broker_transaction_ref": "ref-1",
            "transaction_type": "BUY",
            "trade_datetime": "2026-01-05T00:00:00+00:00",
            "native_currency": "EUR",
            "instrument_identifier": "IWDA.AS",
            "instrument_identifier_kind": "TICKER",
            "quantity": "5",
            "price": "128.65",
        },
    )

    assert response.status_code == 201
    assert captured["native_amount"] is None
