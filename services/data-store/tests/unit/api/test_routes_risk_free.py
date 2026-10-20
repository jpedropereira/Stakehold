from datetime import date
from decimal import Decimal

from data_store import jobs


def make_result(points, first_date=date(1999, 1, 4), source="ecb"):
    return {"source": source, "first_date": first_date, "points": points}


def test_returns_the_spliced_series_with_its_first_date(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_risk_free_series",
        lambda **kw: make_result(
            [
                {"date": date(2019, 9, 30), "rate": Decimal("-0.5530"), "series": "eonia_adjusted"},
                {"date": date(2019, 10, 1), "rate": Decimal("-0.5490"), "series": "estr"},
            ]
        ),
    )

    response = client.get("/rates/risk-free?start=2019-09-27&end=2019-10-04")

    assert response.status_code == 200
    assert response.json() == {
        "source": "ecb",
        "first_date": "1999-01-04",
        "points": [
            # Strings, like every other read model: the NUMERIC scale survives the round trip.
            {"date": "2019-09-30", "rate": "-0.5530", "series": "eonia_adjusted"},
            {"date": "2019-10-01", "rate": "-0.5490", "series": "estr"},
        ],
    }


def test_forwards_the_range_and_a_defaulted_source(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs, "get_risk_free_series", lambda **kw: calls.append(kw) or make_result([])
    )

    client.get("/rates/risk-free?start=2024-01-01&end=2024-01-31")
    client.get("/rates/risk-free?start=2024-01-01&end=2024-01-31&source=test")

    assert calls == [
        {"source": "ecb", "start": date(2024, 1, 1), "end": date(2024, 1, 31)},
        {"source": "test", "start": date(2024, 1, 1), "end": date(2024, 1, 31)},
    ]


def test_an_empty_store_is_a_200_with_no_first_date(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_risk_free_series", lambda **kw: make_result([], first_date=None))

    response = client.get("/rates/risk-free?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 200
    assert response.json() == {"source": "ecb", "first_date": None, "points": []}


def test_start_after_end_is_400(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_risk_free_series", lambda **kw: make_result([]))

    response = client.get("/rates/risk-free?start=2024-02-01&end=2024-01-31")

    assert response.status_code == 400


def test_missing_start_is_422(client):
    response = client.get("/rates/risk-free?end=2024-01-31")

    assert response.status_code == 422
