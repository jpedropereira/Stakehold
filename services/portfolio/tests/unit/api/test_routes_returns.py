import uuid

from portfolio import jobs


def _return_dict(**overrides):
    values = dict(
        account_id=1,
        canonical_ticker="IWDA.AS",
        price_date="2026-01-06",
        quantity="10.000000",
        market_value_native="1000.0000",
        market_value_eur="1000.0000",
        cost_basis_eur="950.0000",
        unrealized_return_eur="50.0000",
        unrealized_return_pct="0.05263158",
        dividends_received_eur="0.0000",
        total_return_eur="50.0000",
        total_return_pct="0.05263158",
    )
    values.update(overrides)
    return values


def test_list_returns_returns_the_jobs_own_list(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_returns", lambda **kw: [_return_dict()])

    response = client.get("/returns", params={"user_id": str(uuid.uuid4()), "account_label": "m"})

    assert response.status_code == 200
    assert response.json() == [_return_dict()]


def test_list_returns_requires_account_label(client):
    response = client.get("/returns", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 422


def test_list_returns_passes_the_ticker_and_date_filters_through(monkeypatch, client):
    captured = {}

    def fake_get_returns(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(jobs, "get_returns", fake_get_returns)
    user_id = uuid.uuid4()

    client.get(
        "/returns",
        params={
            "user_id": str(user_id),
            "account_label": "m",
            "ticker": "IWDA.AS",
            "start": "2026-01-01",
            "end": "2026-01-31",
        },
    )

    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"
    assert captured["canonical_ticker"] == "IWDA.AS"
    assert str(captured["start"]) == "2026-01-01"
    assert str(captured["end"]) == "2026-01-31"


def test_list_returns_filters_default_to_none(monkeypatch, client):
    captured = {}

    def fake_get_returns(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(jobs, "get_returns", fake_get_returns)

    client.get("/returns", params={"user_id": str(uuid.uuid4()), "account_label": "m"})

    assert captured["canonical_ticker"] is None
    assert captured["start"] is None
    assert captured["end"] is None


def test_list_latest_returns_returns_the_jobs_own_list(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_latest_returns", lambda **kw: [_return_dict()])

    response = client.get(
        "/returns/latest", params={"user_id": str(uuid.uuid4()), "account_label": "m"}
    )

    assert response.status_code == 200
    assert response.json() == [_return_dict()]


def test_rebuild_returns_returns_200_even_when_partial(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_rebuild_returns",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 0,
            "tickers_rebuilt": 0,
            "tickers_blocked": 1,
            "pairs_failed": 0,
            "blocked_tickers": [{"account_label": "m", "ticker": "IWDA.AS"}],
            "failed_pairs": [],
        },
    )

    response = client.post(
        "/returns/rebuild", json={"user_id": str(uuid.uuid4()), "account_label": "m"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
