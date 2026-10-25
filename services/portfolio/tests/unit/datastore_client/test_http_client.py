"""HttpDataStoreClient tested against a fake requests.get -- no live data-store instance. See
docs/architecture.md: datastore_client/http_client.py is the one place in portfolio that speaks
HTTP to another service, and the boundary itself (the API contract) isn't this suite's to verify.
"""

from datetime import date
from decimal import Decimal

import pytest
import requests
from portfolio.datastore_client.base import (
    EtfConstituent,
    EtfFund,
    FxPoint,
    PricePoint,
    Regime,
    RegimeList,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
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


def _point(**overrides):
    return {
        "date": "2026-01-08",
        "split_adjusted_close": "95.5000",
        "simple_return": "0.01",
        "volume": 1200,
        **overrides,
    }


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
                    {
                        "date": "2026-01-08",
                        "split_adjusted_close": "95.5000",
                        "simple_return": None,
                        "volume": None,
                    },
                    {
                        "date": "2026-01-09",
                        "split_adjusted_close": "96.2500",
                        "simple_return": "0.007853",
                        "volume": 1200,
                    },
                ],
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    points = client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 9))

    assert points == [
        PricePoint(
            date=date(2026, 1, 8),
            split_adjusted_close=Decimal("95.5000"),
            simple_return=None,
            volume=None,
        ),
        PricePoint(
            date=date(2026, 1, 9),
            split_adjusted_close=Decimal("96.2500"),
            simple_return=Decimal("0.007853"),
            volume=1200,
        ),
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


def test_get_price_series_keeps_a_null_return_as_none_not_zero(client, monkeypatch):
    point = _point(simple_return=None)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [point]}))

    [parsed] = client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))

    assert parsed.simple_return is None


def test_get_price_series_keeps_a_flat_day_as_zero(client, monkeypatch):
    point = _point(simple_return="0")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [point]}))

    [parsed] = client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))

    assert parsed.simple_return == Decimal("0")


def test_get_price_series_raises_on_a_point_missing_simple_return(client, monkeypatch):
    # A missing key must not read as a gap: that would hide a contract break behind wrong returns.
    point = _point()
    del point["simple_return"]
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [point]}))

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))


def test_get_price_series_raises_on_an_unparseable_simple_return(client, monkeypatch):
    point = _point(simple_return="n/a")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [point]}))

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))


def test_get_price_series_keeps_a_zero_volume_as_zero(client, monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [_point(volume=0)]})
    )

    [parsed] = client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))

    assert parsed.volume == 0


def test_get_price_series_keeps_a_null_volume_as_none_not_zero(client, monkeypatch):
    # NULL is data-store's "unknown"; the VaR day mask counts it as traded, unlike a 0.
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [_point(volume=None)]})
    )

    [parsed] = client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))

    assert parsed.volume is None


def test_get_price_series_raises_on_a_point_missing_volume(client, monkeypatch):
    # As with simple_return: defaulting a missing key would hide a contract break.
    point = _point()
    del point["volume"]
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [point]}))

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))


@pytest.mark.parametrize("volume", ["lots", 1.5, True])
def test_get_price_series_raises_on_a_volume_that_is_not_an_integer(client, monkeypatch, volume):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(200, {"points": [_point(volume=volume)]})
    )

    with pytest.raises(DataStoreApiError):
        client.get_price_series("IWDA.AS", date(2026, 1, 8), date(2026, 1, 8))


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


# --- get_risk_free_series ---


def _risk_free_body(points, first_date="1999-01-04"):
    return {"source": "ecb", "first_date": first_date, "points": points}


def test_get_risk_free_series_returns_every_point(client, monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers)
        return FakeResponse(
            200,
            _risk_free_body(
                [
                    {"date": "2026-01-08", "rate": "1.9310", "series": "estr"},
                    {"date": "2026-01-09", "rate": "1.9320", "series": "estr"},
                ]
            ),
        )

    monkeypatch.setattr(requests, "get", fake_get)

    result = client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9))

    assert result == RiskFreeSeries(
        first_date=date(1999, 1, 4),
        points=[
            RiskFreePoint(date=date(2026, 1, 8), rate=Decimal("1.9310"), series="estr"),
            RiskFreePoint(date=date(2026, 1, 9), rate=Decimal("1.9320"), series="estr"),
        ],
    )
    assert captured["url"] == "http://datastore:8000/rates/risk-free"
    assert captured["params"] == {"start": "2026-01-08", "end": "2026-01-09"}
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_get_risk_free_series_keeps_a_negative_rate_as_published(client, monkeypatch):
    # Either side of the splice: EONIA - 0.085 up to 2019-09-30, €STR from 2019-10-01.
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            200,
            _risk_free_body(
                [
                    {"date": "2019-09-30", "rate": "-0.5500", "series": "eonia_adjusted"},
                    {"date": "2019-10-01", "rate": "-0.5490", "series": "estr"},
                ]
            ),
        ),
    )

    result = client.get_risk_free_series(date(2019, 9, 30), date(2019, 10, 1))

    assert [(point.rate, point.series) for point in result.points] == [
        (Decimal("-0.5500"), "eonia_adjusted"),
        (Decimal("-0.5490"), "estr"),
    ]


def test_get_risk_free_series_with_no_points_in_range_still_carries_first_date(client, monkeypatch):
    # A window before 1999 (or a weekend) has no points, but the series exists: R4 needs to
    # know where it begins, so this is a series, not None.
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, _risk_free_body([])))

    result = client.get_risk_free_series(date(1998, 1, 1), date(1998, 12, 31))

    assert result == RiskFreeSeries(first_date=date(1999, 1, 4), points=[])


def test_get_risk_free_series_returns_none_when_nothing_is_stored(client, monkeypatch):
    # first_date null is data-store's "no risk-free data at all" -- never rf = 0.
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(200, _risk_free_body([], first_date=None))
    )

    assert client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9)) is None


def test_get_risk_free_series_raises_on_points_without_a_first_date(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            200,
            _risk_free_body(
                [{"date": "2026-01-08", "rate": "1.9310", "series": "estr"}], first_date=None
            ),
        ),
    )

    with pytest.raises(DataStoreApiError):
        client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9))


@pytest.mark.parametrize("key", ["first_date", "points"])
def test_get_risk_free_series_raises_on_a_response_missing_a_key(client, monkeypatch, key):
    body = _risk_free_body([])
    del body[key]
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, body))

    with pytest.raises(DataStoreApiError):
        client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9))


@pytest.mark.parametrize("key", ["date", "rate", "series"])
def test_get_risk_free_series_raises_on_a_point_missing_a_key(client, monkeypatch, key):
    point = {"date": "2026-01-08", "rate": "1.9310", "series": "estr"}
    del point[key]
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(200, _risk_free_body([point]))
    )

    with pytest.raises(DataStoreApiError):
        client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9))


def test_get_risk_free_series_raises_on_an_unparseable_rate(client, monkeypatch):
    point = {"date": "2026-01-08", "rate": "n/a", "series": "estr"}
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(200, _risk_free_body([point]))
    )

    with pytest.raises(DataStoreApiError):
        client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9))


@pytest.mark.parametrize("status_code", [422, 500])
def test_get_risk_free_series_raises_on_an_error_status(client, monkeypatch, status_code):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(status_code))

    with pytest.raises(DataStoreApiError):
        client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9))


def test_get_risk_free_series_raises_when_the_request_itself_fails(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    with pytest.raises(DataStoreApiError):
        client.get_risk_free_series(date(2026, 1, 8), date(2026, 1, 9))


# --- get_regimes ---


def _regime(**overrides):
    regime = {
        "regime_id": "spx_bear_2020",
        "dimension": "drawdown",
        "scope": "SPX",
        "name": "COVID crash",
        "start_date": "2020-02-19",
        "end_date": "2020-03-23",
        "known_from": None,
        "severity": "severe",
        "source": "manual_approx",
        "notes": None,
    }
    return {**regime, **overrides}


def test_get_regimes_returns_every_regime_with_the_table_total(client, monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers)
        return FakeResponse(
            200,
            {
                "total_regimes": 94,
                "regimes": [
                    _regime(),
                    _regime(
                        regime_id="ukraine_war",
                        dimension="event",
                        scope="GLOBAL",
                        name="Russian invasion of Ukraine",
                        start_date="2022-02-24",
                        end_date=None,
                        known_from="2022-02-24",
                        severity=None,
                        source="manual",
                        notes="ongoing",
                    ),
                ],
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    result = client.get_regimes(date(2020, 1, 1), date(2026, 10, 23))

    assert result == RegimeList(
        total_regimes=94,
        regimes=[
            Regime(
                regime_id="spx_bear_2020",
                dimension="drawdown",
                scope="SPX",
                name="COVID crash",
                start_date=date(2020, 2, 19),
                end_date=date(2020, 3, 23),
                known_from=None,
                severity="severe",
                source="manual_approx",
                notes=None,
            ),
            Regime(
                regime_id="ukraine_war",
                dimension="event",
                scope="GLOBAL",
                name="Russian invasion of Ukraine",
                start_date=date(2022, 2, 24),
                end_date=None,
                known_from=date(2022, 2, 24),
                severity=None,
                source="manual",
                notes="ongoing",
            ),
        ],
    )
    assert captured["url"] == "http://datastore:8000/regimes"
    assert captured["params"] == {"start": "2020-01-01", "end": "2026-10-23"}
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_get_regimes_with_nothing_loaded_reports_a_zero_total(client, monkeypatch):
    # total_regimes 0 is "nothing loaded", which the caller words differently from an empty
    # range -- so it is passed through, not collapsed into an empty list.
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(200, {"total_regimes": 0, "regimes": []}),
    )

    assert client.get_regimes(date(2020, 1, 1), date(2020, 12, 31)) == RegimeList(0, [])


@pytest.mark.parametrize("key", ["total_regimes", "regimes"])
def test_get_regimes_raises_on_a_response_missing_a_key(client, monkeypatch, key):
    body = {"total_regimes": 1, "regimes": [_regime()]}
    del body[key]
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, body))

    with pytest.raises(DataStoreApiError):
        client.get_regimes(date(2020, 1, 1), date(2020, 12, 31))


@pytest.mark.parametrize("key", list(_regime()))
def test_get_regimes_raises_on_a_regime_missing_a_key(client, monkeypatch, key):
    regime = _regime()
    del regime[key]
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(200, {"total_regimes": 1, "regimes": [regime]}),
    )

    with pytest.raises(DataStoreApiError):
        client.get_regimes(date(2020, 1, 1), date(2020, 12, 31))


@pytest.mark.parametrize(
    "overrides", [{"start_date": "19/02/2020"}, {"end_date": "soon"}, {"known_from": 2020}]
)
def test_get_regimes_raises_on_an_unparseable_date(client, monkeypatch, overrides):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(200, {"total_regimes": 1, "regimes": [_regime(**overrides)]}),
    )

    with pytest.raises(DataStoreApiError):
        client.get_regimes(date(2020, 1, 1), date(2020, 12, 31))


@pytest.mark.parametrize("status_code", [422, 500])
def test_get_regimes_raises_on_an_error_status(client, monkeypatch, status_code):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(status_code))

    with pytest.raises(DataStoreApiError):
        client.get_regimes(date(2020, 1, 1), date(2020, 12, 31))


def test_get_regimes_raises_when_the_request_itself_fails(client, monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    with pytest.raises(DataStoreApiError):
        client.get_regimes(date(2020, 1, 1), date(2020, 12, 31))


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
                "currency": "GBp",
                "fx_currency": "GBP",
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    info = client.get_ticker_info("IWDA.AS")

    assert info == TickerInfo(exists=True, has_returns=True, currency="GBp", fx_currency="GBP")
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


@pytest.mark.parametrize(
    "currency_fields",
    [{"currency": None, "fx_currency": None}, {}],
    ids=["null", "absent"],
)
def test_get_ticker_info_missing_currency_is_none(client, monkeypatch, currency_fields):
    body = {"exists": True, "has_returns": True, **currency_fields}
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, body))

    info = client.get_ticker_info("ODD.L")

    assert info.currency is None
    assert info.fx_currency is None


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


# Labs (LB-4) hands these two methods tickers a user typed, not ones read from stored positions,
# so a "/" must stay inside one path segment rather than reach another data-store route.


def test_get_ticker_info_quotes_the_ticker_as_one_path_segment(client, monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResponse(200, {"exists": False, "has_returns": False})

    monkeypatch.setattr(requests, "get", fake_get)

    client.get_ticker_info("A/../B")

    assert captured["url"] == "http://datastore:8000/tickers/A%2F..%2FB"


def test_get_price_series_quotes_the_ticker_as_one_path_segment(client, monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResponse(200, {"points": []})

    monkeypatch.setattr(requests, "get", fake_get)

    client.get_price_series("A/../B", date(2026, 1, 1), date(2026, 1, 9))

    assert captured["url"] == "http://datastore:8000/prices/A%2F..%2FB"


# --- ETF holdings (RK-22) ---------------------------------------------------------------------

_FUND = {
    "fund_key": "ishares_msci_world",
    "display_name": "iShares MSCI World UCITS ETF (Acc)",
    "ticker": "IWDA.AS",
    "enabled": True,
    "latest_as_of_date": "2026-08-30",
}

_CONSTITUENT = {
    "ticker": "BRKB",
    "yfinance_ticker": "BRK-B",
    "name": "BERKSHIRE HATHAWAY INC CLASS B",
    "sector": "Financials",
    "asset_class": "Equity",
    "location": "United States",
    "exchange": "New York Stock Exchange Inc.",
    "currency": "USD",
    "weight_percent": "0.7600",
}


def test_list_etf_funds_parses_every_fund(client, monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        never_scraped = {**_FUND, "fund_key": "other", "ticker": None, "latest_as_of_date": None}
        return FakeResponse(200, {"funds": [_FUND, never_scraped]})

    monkeypatch.setattr(requests, "get", fake_get)

    funds = client.list_etf_funds()

    assert captured["url"] == "http://datastore:8000/etf-holdings"
    assert funds == [
        EtfFund(
            fund_key="ishares_msci_world",
            display_name="iShares MSCI World UCITS ETF (Acc)",
            ticker="IWDA.AS",
            enabled=True,
            latest_as_of_date=date(2026, 8, 30),
        ),
        EtfFund(
            fund_key="other",
            display_name="iShares MSCI World UCITS ETF (Acc)",
            ticker=None,
            enabled=True,
            latest_as_of_date=None,
        ),
    ]


def test_list_etf_funds_raises_on_a_fund_missing_a_key(client, monkeypatch):
    fund = {k: v for k, v in _FUND.items() if k != "ticker"}
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, {"funds": [fund]}))

    with pytest.raises(DataStoreApiError):
        client.list_etf_funds()


def test_list_etf_funds_raises_on_a_server_error(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(500))

    with pytest.raises(DataStoreApiError):
        client.list_etf_funds()


def test_get_latest_etf_holdings_parses_the_snapshot(client, monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        cash = {
            **_CONSTITUENT,
            "ticker": None,
            "yfinance_ticker": None,
            "asset_class": "Cash",
            "weight_percent": None,
        }
        return FakeResponse(
            200,
            {
                "fund_key": "ishares_msci_world",
                "ticker": "IWDA.AS",
                "as_of_date": "2026-08-30",
                "constituents": [_CONSTITUENT, cash],
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)

    holdings = client.get_latest_etf_holdings("ishares_msci_world")

    assert captured["url"] == "http://datastore:8000/etf-holdings/ishares_msci_world/latest"
    assert holdings.fund_key == "ishares_msci_world"
    assert holdings.ticker == "IWDA.AS"
    assert holdings.as_of_date == date(2026, 8, 30)
    assert holdings.constituents[0] == EtfConstituent(
        ticker="BRKB",
        yfinance_ticker="BRK-B",
        name="BERKSHIRE HATHAWAY INC CLASS B",
        sector="Financials",
        asset_class="Equity",
        location="United States",
        exchange="New York Stock Exchange Inc.",
        currency="USD",
        weight_percent=Decimal("0.7600"),
    )
    assert holdings.constituents[1].weight_percent is None
    assert holdings.constituents[1].ticker is None


def test_get_latest_etf_holdings_returns_none_on_a_404(client, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(404))

    assert client.get_latest_etf_holdings("ishares_msci_world") is None


def test_get_latest_etf_holdings_raises_on_a_constituent_missing_a_key(client, monkeypatch):
    constituent = {k: v for k, v in _CONSTITUENT.items() if k != "yfinance_ticker"}
    body = {
        "fund_key": "f",
        "ticker": None,
        "as_of_date": "2026-08-30",
        "constituents": [constituent],
    }
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, body))

    with pytest.raises(DataStoreApiError):
        client.get_latest_etf_holdings("f")


def test_get_latest_etf_holdings_raises_on_an_unparseable_weight(client, monkeypatch):
    body = {
        "fund_key": "f",
        "ticker": None,
        "as_of_date": "2026-08-30",
        "constituents": [{**_CONSTITUENT, "weight_percent": "abc"}],
    }
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(200, body))

    with pytest.raises(DataStoreApiError):
        client.get_latest_etf_holdings("f")


def test_get_latest_etf_holdings_quotes_the_fund_key(client, monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        return FakeResponse(404)

    monkeypatch.setattr(requests, "get", fake_get)
    client.get_latest_etf_holdings("a/b")

    assert captured["url"] == "http://datastore:8000/etf-holdings/a%2Fb/latest"
