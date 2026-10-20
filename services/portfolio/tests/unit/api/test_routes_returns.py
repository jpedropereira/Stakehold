import uuid
from datetime import date

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
            "tickers_removed": 0,
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


def test_rebuild_returns_returns_201_when_ok(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_rebuild_returns",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 3,
            "tickers_rebuilt": 1,
            "tickers_removed": 2,
            "tickers_blocked": 0,
            "pairs_failed": 0,
            "blocked_tickers": [],
            "failed_pairs": [],
        },
    )

    response = client.post(
        "/returns/rebuild", json={"user_id": str(uuid.uuid4()), "account_label": "m"}
    )

    assert response.status_code == 201
    assert response.json()["tickers_removed"] == 2


# --- GET /returns/portfolio ---

_PORTFOLIO_RETURNS = {
    "series": [
        {
            "price_date": "2026-01-06",
            "market_value_eur": "2200.0000",
            "cost_basis_eur": "2000.0000",
            "dividends_received_eur": "0.0000",
            "total_return_eur": "200.0000",
            "total_return_pct": "0.10000000",
        }
    ],
}


def test_portfolio_returns_passes_the_range_through_and_returns_the_jobs_own_dict(
    monkeypatch, client
):
    captured = {}

    def fake_get_portfolio_returns(**kw):
        captured.update(kw)
        return _PORTFOLIO_RETURNS

    monkeypatch.setattr(jobs, "get_portfolio_returns", fake_get_portfolio_returns)
    user_id = uuid.uuid4()

    response = client.get(
        "/returns/portfolio",
        params={"user_id": str(user_id), "start": "2026-01-01", "end": "2026-02-01"},
    )

    assert response.status_code == 200
    assert response.json() == _PORTFOLIO_RETURNS
    assert captured["user_id"] == user_id
    assert captured["start"] == date(2026, 1, 1)
    assert captured["end"] == date(2026, 2, 1)


def test_portfolio_returns_range_is_optional(monkeypatch, client):
    captured = {}

    def fake_get_portfolio_returns(**kw):
        captured.update(kw)
        return {"series": []}

    monkeypatch.setattr(jobs, "get_portfolio_returns", fake_get_portfolio_returns)

    response = client.get("/returns/portfolio", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json() == {"series": []}
    assert captured["start"] is None
    assert captured["end"] is None


def test_portfolio_returns_serves_a_null_pct_when_nothing_has_cost(monkeypatch, client):
    body = {
        "series": [{**_PORTFOLIO_RETURNS["series"][0], "total_return_pct": None}],
    }
    monkeypatch.setattr(jobs, "get_portfolio_returns", lambda **kw: body)

    response = client.get("/returns/portfolio", params={"user_id": str(uuid.uuid4())})

    assert response.json()["series"][0]["total_return_pct"] is None


def test_portfolio_returns_requires_user_id(client):
    response = client.get("/returns/portfolio")

    assert response.status_code == 422


# --- GET /returns/portfolio/twr ---

_PORTFOLIO_TWR = {
    "base_date": "2026-01-05",
    "points": [
        {"price_date": "2026-01-06", "twr_return": None, "twr_growth": "1.00000000"},
        {"price_date": "2026-01-07", "twr_return": "0.01000000", "twr_growth": "1.01000000"},
    ],
    "n_gaps": 1,
    "n_excluded": 1,
    "excluded": [{"ticker": "U", "status": "no_fx_rate", "reason": "no FX series for HKD"}],
}


def test_portfolio_twr_passes_the_user_through_and_returns_the_jobs_own_dict(monkeypatch, client):
    captured = {}

    def fake_get_portfolio_twr(**kw):
        captured.update(kw)
        return _PORTFOLIO_TWR

    monkeypatch.setattr(jobs, "get_portfolio_twr", fake_get_portfolio_twr)
    user_id = uuid.uuid4()

    response = client.get("/returns/portfolio/twr", params={"user_id": str(user_id)})

    assert response.status_code == 200
    assert response.json() == _PORTFOLIO_TWR
    assert captured["user_id"] == user_id


def test_portfolio_twr_requires_a_user(client):
    response = client.get("/returns/portfolio/twr")

    assert response.status_code == 422
