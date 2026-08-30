from datetime import date

from data_store import jobs
from data_store.read_flows import CurrencyNotTracked


def make_fx_result(base, quote, derivation, via, points):
    return {
        "base_currency": base,
        "quote_currency": quote,
        "source": "yfinance",
        "derivation": derivation,
        "via": via,
        "points": points,
    }


def test_base_and_quote_are_forwarded_independently_not_collapsed_to_the_same_value(
    monkeypatch, client
):
    """Regression: base and quote share a Path(...) validation shape (same pattern), but a
    shared *instance* of that Path(...) object as both parameters' default previously made
    FastAPI resolve both to the same value -- /fx/EUR/USD silently became base=quote=EUR."""
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_fx_series",
        lambda **kw: calls.append(kw)
        or make_fx_result(kw["base"], kw["quote"], "direct", None, []),
    )

    client.get("/fx/EUR/USD?start=2024-01-01&end=2024-01-31")

    assert calls[0]["base"] == "EUR"
    assert calls[0]["quote"] == "USD"


def test_direct_pair(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_fx_series",
        lambda **kw: make_fx_result(
            "EUR", "USD", "direct", None, [{"date": date(2024, 1, 2), "rate": "1.10000000"}]
        ),
    )

    response = client.get("/fx/EUR/USD?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 200
    body = response.json()
    assert body["derivation"] == "direct"
    assert body["via"] is None
    assert body["points"][0]["rate"] == "1.10000000"


def test_triangulated_pair_reports_via(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_fx_series",
        lambda **kw: make_fx_result(
            "GBP", "CHF", "triangulated", "EUR", [{"date": date(2024, 1, 2), "rate": "1.1176"}]
        ),
    )

    response = client.get("/fx/GBP/CHF?start=2024-01-01&end=2024-01-31")

    body = response.json()
    assert body["derivation"] == "triangulated"
    assert body["via"] == "EUR"


def test_untracked_currency_is_404(monkeypatch, client):
    def raise_not_tracked(**kw):
        raise CurrencyNotTracked("JPY")

    monkeypatch.setattr(jobs, "get_fx_series", raise_not_tracked)

    response = client.get("/fx/JPY/USD?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 404


def test_lowercase_currency_code_is_422(client):
    response = client.get("/fx/eur/usd?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 422


def test_four_letter_currency_code_is_422(client):
    response = client.get("/fx/EURO/USD?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 422


def test_start_after_end_is_400(client):
    response = client.get("/fx/EUR/USD?start=2024-02-01&end=2024-01-01")

    assert response.status_code == 400
