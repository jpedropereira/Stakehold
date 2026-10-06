"""HttpDataStoreClient tested against a fake requests.get -- no live data-store instance. See
docs/architecture.md: datastore_client/http_client.py is the one place in portfolio that speaks
HTTP to another service, and the boundary itself (the API contract) isn't this suite's to verify.
"""

from datetime import date
from decimal import Decimal

import pytest
import requests
from portfolio.datastore_client.base import FxPoint, PricePoint, TickerInfo
from portfolio.datastore_client.http_client import HttpDataStoreClient
from portfolio.errors import DataStoreApiError


class FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


@pytest.fixture
def client():
    return HttpDataStoreClient(
        base_url="http://datastore:8000", internal_api_secret="secret"  # pragma: allowlist secret
    )


def test_get_fx_rate_returns_the_latest_point_at_or_before_on_date(client, monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResponse(
            200,
            {
                "base_currency": "EUR",
                "quote_currency": "USD",
                "points": [
                    {"date": "2026-01-08", "rate": "1.09000000"},
                    {"date": "2026-01-09", "rate": "1.10000000"},
                ],
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    rate = client.get_fx_rate("EUR", "USD", date(2026, 1, 9))

    assert rate == Decimal("1.10000000")
    assert captured["url"] == "http://datastore:8000/fx/EUR/USD"
    assert captured["params"]["end"] == "2026-01-09"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_get_fx_rate_requests_a_lookback_window_ending_on_the_requested_date(client, monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(params=params)
        return FakeResponse(200, {"points": []})

    monkeypatch.setattr(requests, "get", fake_get)

    client.get_fx_rate("EUR", "PLN", date(2026, 1, 9))

    assert captured["params"]["end"] == "2026-01-09"
    assert captured["params"]["start"] < captured["params"]["end"]


def test_get_fx_rate_returns_none_when_no_points_in_range(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": []}))

    assert client.get_fx_rate("EUR", "USD", date(2026, 1, 9)) is None


def test_get_fx_rate_returns_none_on_a_404_untracked_currency(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(404))

    assert client.get_fx_rate("EUR", "XXX", date(2026, 1, 9)) is None


def test_get_fx_rate_raises_on_a_server_error(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(500))

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate("EUR", "USD", date(2026, 1, 9))


def test_get_fx_rate_raises_on_a_response_missing_the_points_key(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {}))

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate("EUR", "USD", date(2026, 1, 9))


def test_get_fx_rate_raises_on_a_point_missing_the_rate_key(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(200, {"points": [{"date": "2026-01-09"}]}),
    )

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate("EUR", "USD", date(2026, 1, 9))


def test_get_fx_rate_raises_on_a_point_with_an_unparseable_rate(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            200, {"points": [{"date": "2026-01-09", "rate": "not-a-number"}]}
        ),
    )

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate("EUR", "USD", date(2026, 1, 9))


def test_get_fx_rate_raises_when_the_request_itself_fails(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate("EUR", "USD", date(2026, 1, 9))


def test_base_url_trailing_slash_is_stripped(monkeypatch):
    client = HttpDataStoreClient(base_url="http://datastore:8000/", internal_api_secret="secret")
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        return FakeResponse(200, {"points": []})

    monkeypatch.setattr(requests, "get", fake_get)

    client.get_fx_rate("EUR", "USD", date(2026, 1, 9))

    assert captured["url"] == "http://datastore:8000/fx/EUR/USD"


# --- get_price_series ---


def test_get_price_series_returns_every_point(client, monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers)
        return FakeResponse(
            200,
            {
                "ticker": "IWDA.AS",
                "source": "yfinance",
                "points": [
                    {"date": "2026-01-08", "split_adjusted_close": "95.5000"},
                    {"date": "2026-01-09", "split_adjusted_close": "96.2500"},
                ],
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    points = client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 9))

    assert points == [
        PricePoint(date=date(2026, 1, 8), split_adjusted_close=Decimal("95.5000")),
        PricePoint(date=date(2026, 1, 9), split_adjusted_close=Decimal("96.2500")),
    ]
    assert captured["url"] == "http://datastore:8000/prices/IWDA.AS"
    assert captured["params"] == {"start": "2026-01-08", "end": "2026-01-09"}
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_get_price_series_returns_an_empty_list_for_no_data_in_range(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": []}))

    assert client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 9)) == []


def test_get_price_series_raises_on_a_404_unknown_ticker(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(404))

    with pytest.raises(DataStoreApiError):
        client.get_price_series("NOPE", date(2026, 1, 8), date(2026, 1, 9))


def test_get_price_series_raises_on_a_server_error(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(500))

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 9))


def test_get_price_series_raises_on_a_response_missing_the_points_key(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {}))

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 9))


def test_get_price_series_raises_on_a_point_missing_split_adjusted_close(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(200, {"points": [{"date": "2026-01-08"}]}),
    )

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 9))


def test_get_price_series_raises_when_the_request_itself_fails(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 9))


# --- get_fx_rate_series ---


def test_get_fx_rate_series_returns_every_point(client, monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, params=params)
        return FakeResponse(
            200,
            {
                "points": [
                    {"date": "2026-01-08", "rate": "1.09000000"},
                    {"date": "2026-01-09", "rate": "1.10000000"},
                ]
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    points = client.get_fx_rate_series("EUR", "USD", date(2026, 1, 8), date(2026, 1, 9))

    assert points == [
        FxPoint(date=date(2026, 1, 8), rate=Decimal("1.09000000")),
        FxPoint(date=date(2026, 1, 9), rate=Decimal("1.10000000")),
    ]
    assert captured["url"] == "http://datastore:8000/fx/EUR/USD"
    assert captured["params"] == {"start": "2026-01-08", "end": "2026-01-09"}


def test_get_fx_rate_series_returns_an_empty_list_for_no_data_in_range(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": []}))

    assert client.get_fx_rate_series("EUR", "USD", date(2026, 1, 8), date(2026, 1, 9)) == []


def test_get_fx_rate_series_returns_an_empty_list_on_a_404_untracked_currency(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(404))

    assert client.get_fx_rate_series("EUR", "XXX", date(2026, 1, 8), date(2026, 1, 9)) == []


def test_get_fx_rate_series_raises_on_a_server_error(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(500))

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate_series("EUR", "USD", date(2026, 1, 8), date(2026, 1, 9))


def test_get_fx_rate_series_raises_on_a_point_missing_the_rate_key(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(200, {"points": [{"date": "2026-01-08"}]}),
    )

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate_series("EUR", "USD", date(2026, 1, 8), date(2026, 1, 9))


def test_get_fx_rate_series_raises_when_the_request_itself_fails(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    with pytest.raises(DataStoreApiError):
        client.get_fx_rate_series("EUR", "USD", date(2026, 1, 8), date(2026, 1, 9))


# --- get_ticker_info ---


def test_get_ticker_info_returns_exists_and_has_returns(client, monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured.update(url=url, headers=headers)
        return FakeResponse(
            200,
            {
                "ticker": "IWDA.AS",
                "exists": True,
                "has_returns": True,
                "first_date": "2020-01-01",
                "last_date": "2026-01-09",
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    info = client.get_ticker_info("IWDA.AS")

    assert info == TickerInfo(exists=True, has_returns=True)
    assert captured["url"] == "http://datastore:8000/tickers/IWDA.AS"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_get_ticker_info_never_404s_exists_false_is_a_normal_200(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            200, {"exists": False, "has_returns": False, "first_date": None, "last_date": None}
        ),
    )

    info = client.get_ticker_info("NOPE")

    assert info == TickerInfo(exists=False, has_returns=False)


def test_get_ticker_info_raises_on_a_server_error(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(500))

    with pytest.raises(DataStoreApiError):
        client.get_ticker_info("IWDA.AS")


def test_get_ticker_info_raises_on_a_response_missing_expected_keys(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {}))

    with pytest.raises(DataStoreApiError):
        client.get_ticker_info("IWDA.AS")


def test_get_ticker_info_raises_when_the_request_itself_fails(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    with pytest.raises(DataStoreApiError):
        client.get_ticker_info("IWDA.AS")


# --- list_tickers ---


def test_list_tickers_returns_the_ticker_list(client, monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured.update(url=url, headers=headers)
        return FakeResponse(200, {"tickers": ["AAPL", "IWDA.AS"]})

    monkeypatch.setattr(requests, "get", fake_get)

    assert client.list_tickers() == ["AAPL", "IWDA.AS"]
    assert captured["url"] == "http://datastore:8000/tickers"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_list_tickers_raises_on_a_server_error(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(500))

    with pytest.raises(DataStoreApiError):
        client.list_tickers()


def test_list_tickers_raises_on_a_malformed_response(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"tickers": [1]}))

    with pytest.raises(DataStoreApiError):
        client.list_tickers()


def test_list_tickers_raises_when_the_request_itself_fails(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    with pytest.raises(DataStoreApiError):
        client.list_tickers()
