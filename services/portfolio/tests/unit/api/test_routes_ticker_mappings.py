"""Route wiring only. How each domain error becomes a status code is covered for every
PortfolioError subclass by test_app.py, and end to end by tests/integration/test_api.py."""

import uuid

from portfolio import jobs

_ADMIN = uuid.UUID("00000000-0000-7000-8000-000000000001")
_MAPPING = {
    "id": 7,
    "identifier_kind": "ISIN",
    "identifier": "IE00B4L5Y983",
    "broker": None,
    "canonical_ticker": "IWDA.AS",
    "updated_by": str(_ADMIN),
    "created_at": "2026-10-01T00:00:00+00:00",
    "updated_at": "2026-10-06T00:00:00+00:00",
}
_WRITE_RESULT = {
    "status": "ok",
    "rows_saved": 1,
    "accounts_rebuilt": 2,
    "rebuilds_failed": 0,
    "returns_rebuilds_failed": 0,
    "mapping": _MAPPING,
}
_SET_BODY = {
    "identifier_kind": "ISIN",
    "identifier": "IE00B4L5Y983",
    "broker": None,
    "canonical_ticker": "IWDA.AS",
    "updated_by": str(_ADMIN),
}


def _expected_mapping():
    return {
        **_MAPPING,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-06T00:00:00Z",
    }


def test_list_ticker_mappings_returns_every_mapping(monkeypatch, client):
    monkeypatch.setattr(jobs, "get_ticker_mappings", lambda **kw: [_MAPPING])

    response = client.get("/ticker-mappings")

    assert response.status_code == 200
    assert response.json() == [_expected_mapping()]


def test_set_ticker_mapping_passes_the_body_through_and_reports_the_rebuild(monkeypatch, client):
    captured = {}

    def fake_set(**kw):
        captured.update(kw)
        return _WRITE_RESULT

    monkeypatch.setattr(jobs, "run_set_ticker_mapping", fake_set)

    response = client.put("/ticker-mappings", json=_SET_BODY)

    assert response.status_code == 200
    assert response.json() == {**_WRITE_RESULT, "mapping": _expected_mapping()}
    assert captured["identifier_kind"] == "ISIN"
    assert captured["broker"] is None
    assert captured["updated_by"] == _ADMIN
    assert captured["settings"] is not None


def test_set_ticker_mapping_requires_updated_by(client):
    body = {k: v for k, v in _SET_BODY.items() if k != "updated_by"}

    assert client.put("/ticker-mappings", json=body).status_code == 422


def test_delete_ticker_mapping_passes_the_id_and_admin_through(monkeypatch, client):
    captured = {}

    def fake_delete(**kw):
        captured.update(kw)
        return _WRITE_RESULT

    monkeypatch.setattr(jobs, "run_delete_ticker_mapping", fake_delete)

    response = client.delete("/ticker-mappings/7", params={"updated_by": str(_ADMIN)})

    assert response.status_code == 200
    assert response.json()["mapping"] == _expected_mapping()
    assert captured["mapping_id"] == 7
    assert captured["updated_by"] == _ADMIN


def test_delete_ticker_mapping_requires_updated_by(client):
    assert client.delete("/ticker-mappings/7").status_code == 422


def test_list_all_unmapped_instruments_returns_the_global_list(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "run_list_all_unmapped_instruments",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 0,
            "instruments_unmapped": 2,
            "unmapped_instruments": [
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": None},
                {"kind": "SYMBOL", "identifier": "SGLD", "broker": "globex"},
            ],
        },
    )

    response = client.get("/instruments/unmapped/all")

    assert response.status_code == 200
    assert response.json() == [
        {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": None},
        {"kind": "SYMBOL", "identifier": "SGLD", "broker": "globex"},
    ]
