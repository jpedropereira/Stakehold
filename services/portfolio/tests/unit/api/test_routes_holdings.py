import uuid

from portfolio import jobs


def _holding_dict():
    return dict(
        canonical_ticker="IWDA.AS",
        quantity="140.000000",
        average_cost_eur="82.142857",
        cost_basis_eur="11500.0000",
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native="82.142857",
        market_value_native="14000.0000",
        priced=True,
        price_date="2026-01-06",
        market_value_eur="14000.0000",
        unrealized_return_eur="2500.0000",
        unrealized_return_pct="0.21739130",
        dividends_received_eur="100.0000",
        total_return_eur="2600.0000",
        total_return_pct="0.22608696",
        accounts=[
            dict(
                account_label="A",
                quantity="120.000000",
                native_currency="EUR",
                average_cost_native="80.000000",
                cost_basis_eur="9600.0000",
                market_value_native=None,
                market_value_eur=None,
                unrealized_return_eur=None,
            )
        ],
    )


def _totals_dict():
    return dict(
        price_date="2026-01-06",
        market_value_eur="14000.0000",
        cost_basis_eur="11500.0000",
        dividends_received_eur="100.0000",
        total_return_eur="2600.0000",
        total_return_pct=None,
    )


def test_list_holdings_returns_the_jobs_own_result(monkeypatch, client):
    body = {"holdings": [_holding_dict()], "totals": _totals_dict()}
    monkeypatch.setattr(jobs, "get_holdings", lambda **kw: body)

    response = client.get("/holdings", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json() == body


def test_list_holdings_serves_an_empty_portfolio(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_holdings", lambda **kw: {"holdings": [], "totals": None})

    response = client.get("/holdings", params={"user_id": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json() == {"holdings": [], "totals": None}


def test_list_holdings_passes_the_user_through(monkeypatch, client):
    captured = {}

    def fake_get_holdings(**kw):
        captured.update(kw)
        return {"holdings": [], "totals": None}

    monkeypatch.setattr(jobs, "get_holdings", fake_get_holdings)
    user_id = uuid.uuid4()

    client.get("/holdings", params={"user_id": str(user_id)})

    assert captured["user_id"] == user_id


def test_list_holdings_requires_a_user_id(client):
    response = client.get("/holdings")

    assert response.status_code == 422
