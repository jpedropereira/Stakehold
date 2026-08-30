from datetime import date

from data_store import jobs
from data_store.read_flows import TickerNotFound


def test_returns_points_with_nulls_preserved_as_json_null(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_price_series",
        lambda **kw: {
            "ticker": "AAPL",
            "source": "yfinance",
            "points": [
                {
                    "date": date(2024, 1, 2),
                    "split_adjusted_close": "100.0000",
                    "future_split_factor": "1.000000000000000",
                    "price_return": None,
                    "dividend_return": None,
                    "simple_return": None,
                    "log_return": None,
                }
            ],
        },
    )

    response = client.get("/prices/AAPL?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AAPL"
    point = body["points"][0]
    assert point["price_return"] is None
    assert point["split_adjusted_close"] == "100.0000"


def test_preserves_decimal_precision_as_a_string_not_a_rounded_float(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_price_series",
        lambda **kw: {
            "ticker": "PLS.AX",
            "source": "yfinance",
            "points": [
                {
                    "date": date(2024, 1, 2),
                    "split_adjusted_close": "0.0093",
                    "future_split_factor": "1.000000000000000",
                    "price_return": "0.01",
                    "dividend_return": "0",
                    "simple_return": "0.01",
                    "log_return": "0.00995",
                }
            ],
        },
    )

    response = client.get("/prices/PLS.AX?start=2024-01-01&end=2024-01-31")

    assert response.json()["points"][0]["split_adjusted_close"] == "0.0093"


def test_empty_range_returns_200_with_empty_points_not_an_error(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_price_series",
        lambda **kw: {"ticker": "AAPL", "source": "yfinance", "points": []},
    )

    response = client.get("/prices/AAPL?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 200
    assert response.json()["points"] == []


def test_unknown_ticker_is_404(monkeypatch, client):
    def raise_not_found(**kw):
        raise TickerNotFound("NOPE")

    monkeypatch.setattr(jobs, "get_price_series", raise_not_found)

    response = client.get("/prices/NOPE?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 404


def test_start_after_end_is_400(client):
    response = client.get("/prices/AAPL?start=2024-02-01&end=2024-01-01")

    assert response.status_code == 400


def test_source_defaults_to_yfinance(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_price_series",
        lambda **kw: calls.append(kw) or {"ticker": "AAPL", "source": "yfinance", "points": []},
    )

    client.get("/prices/AAPL?start=2024-01-01&end=2024-01-31")

    assert calls[0]["source"] == "yfinance"


def test_source_is_overridable(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_price_series",
        lambda **kw: calls.append(kw) or {"ticker": "AAPL", "source": "other", "points": []},
    )

    client.get("/prices/AAPL?start=2024-01-01&end=2024-01-31&source=other")

    assert calls[0]["source"] == "other"


def test_ticker_too_long_is_422(client):
    response = client.get("/prices/" + "A" * 21 + "?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 422
