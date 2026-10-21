from datetime import date

from data_store import jobs


def make_result(regimes, total_regimes=93):
    return {"total_regimes": total_regimes, "regimes": regimes}


def test_returns_overlapping_regimes_with_the_table_total(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_regimes",
        lambda **kw: make_result(
            [
                {
                    "regime_id": "spx_bear_2020",
                    "dimension": "drawdown",
                    "scope": "SPX",
                    "name": "COVID crash",
                    "start_date": date(2020, 2, 19),
                    "end_date": date(2020, 3, 23),
                    "known_from": date(2020, 3, 23),
                    "severity": "severe",
                    "source": "manual_approx",
                    "notes": "about -34%",
                },
                {
                    "regime_id": "ongoing",
                    "dimension": "inflation",
                    "scope": "EA",
                    "name": "Ongoing",
                    "start_date": date(2022, 1, 1),
                    "end_date": None,
                    "known_from": None,
                    "severity": None,
                    "source": "manual_approx",
                    "notes": None,
                },
            ]
        ),
    )

    response = client.get("/regimes?start=2020-01-01&end=2024-12-31")

    assert response.status_code == 200
    assert response.json() == {
        "total_regimes": 93,
        "regimes": [
            {
                "regime_id": "spx_bear_2020",
                "dimension": "drawdown",
                "scope": "SPX",
                "name": "COVID crash",
                "start_date": "2020-02-19",
                "end_date": "2020-03-23",
                "known_from": "2020-03-23",
                "severity": "severe",
                "source": "manual_approx",
                "notes": "about -34%",
            },
            {
                # An ongoing regime: no end date, and nothing optional filled in.
                "regime_id": "ongoing",
                "dimension": "inflation",
                "scope": "EA",
                "name": "Ongoing",
                "start_date": "2022-01-01",
                "end_date": None,
                "known_from": None,
                "severity": None,
                "source": "manual_approx",
                "notes": None,
            },
        ],
    }


def test_forwards_the_range(monkeypatch, client):
    calls = []
    monkeypatch.setattr(jobs, "get_regimes", lambda **kw: calls.append(kw) or make_result([]))

    client.get("/regimes?start=2024-01-01&end=2024-01-31")

    assert calls == [{"start": date(2024, 1, 1), "end": date(2024, 1, 31)}]


def test_an_empty_table_is_a_200_with_a_zero_total(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_regimes", lambda **kw: make_result([], total_regimes=0))

    response = client.get("/regimes?start=2024-01-01&end=2024-01-31")

    assert response.status_code == 200
    assert response.json() == {"total_regimes": 0, "regimes": []}


def test_start_after_end_is_400(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_regimes", lambda **kw: make_result([]))

    response = client.get("/regimes?start=2024-02-01&end=2024-01-31")

    assert response.status_code == 400


def test_missing_start_is_422(client):
    response = client.get("/regimes?end=2024-01-31")

    assert response.status_code == 422
