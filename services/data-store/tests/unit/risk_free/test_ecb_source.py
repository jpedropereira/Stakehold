from datetime import date

import pytest
import requests
from data_store.risk_free.ecb_source import ECB_SERIES, EcbRiskFreeRateSource

# Recorded from the ECB Data Portal API on 2026-10-20:
# GET .../service/data/EST/B.EU000A2X2A25.WT?format=csvdata&detail=dataonly
#     &startPeriod=2019-10-01&endPeriod=2019-10-04
ESTR_FIRST_DAYS_CSV = (
    "KEY,FREQ,BENCHMARK_ITEM,DATA_TYPE_EST,TIME_PERIOD,OBS_VALUE\n"
    "EST.B.EU000A2X2A25.WT,B,EU000A2X2A25,WT,2019-10-01,-0.549\n"
    "EST.B.EU000A2X2A25.WT,B,EU000A2X2A25,WT,2019-10-02,-0.551\n"
    "EST.B.EU000A2X2A25.WT,B,EU000A2X2A25,WT,2019-10-03,-0.555\n"
    "EST.B.EU000A2X2A25.WT,B,EU000A2X2A25,WT,2019-10-04,-0.553\n"
)


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class FakeSession:
    """Answers each get() with the next queued response, or raises it if it's an exception."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("data_store.provider_retry.time.sleep", lambda _seconds: None)


def make_source(session, **kwargs):
    return EcbRiskFreeRateSource(session=session, **kwargs)


def test_name_is_ecb():
    assert EcbRiskFreeRateSource.name == "ecb"


def test_series_keys_are_the_verified_ones():
    assert ECB_SERIES == {
        "estr": "EST/B.EU000A2X2A25.WT",
        "eonia": "EON/D.EONIA_TO.RATE",
    }


def test_fetch_parses_a_recorded_response_into_long_form_rows():
    session = FakeSession(FakeResponse(text=ESTR_FIRST_DAYS_CSV))

    result = make_source(session).fetch("estr", date(2019, 10, 1), date(2019, 10, 4))

    assert list(result.columns) == ["series", "rate_date", "rate"]
    assert result.to_dict("records") == [
        {"series": "estr", "rate_date": date(2019, 10, 1), "rate": -0.549},
        {"series": "estr", "rate_date": date(2019, 10, 2), "rate": -0.551},
        {"series": "estr", "rate_date": date(2019, 10, 3), "rate": -0.555},
        {"series": "estr", "rate_date": date(2019, 10, 4), "rate": -0.553},
    ]


def test_fetch_asks_for_the_series_key_and_the_inclusive_date_range():
    session = FakeSession(FakeResponse(text=ESTR_FIRST_DAYS_CSV))

    make_source(session).fetch("eonia", date(1999, 1, 4), date(2021, 12, 31))

    (call,) = session.calls
    assert call["url"] == "https://data-api.ecb.europa.eu/service/data/EON/D.EONIA_TO.RATE"
    assert call["params"] == {
        "format": "csvdata",
        "detail": "dataonly",
        "startPeriod": "1999-01-04",
        "endPeriod": "2021-12-31",
    }
    assert call["timeout"] is not None


def test_fetch_returns_an_empty_frame_for_an_empty_body():
    # What the portal answers for a range with no published days: 200, no body at all.
    session = FakeSession(FakeResponse(text=""))

    result = make_source(session).fetch("estr", date(2019, 10, 5), date(2019, 10, 6))

    assert result.empty
    assert list(result.columns) == ["series", "rate_date", "rate"]


def test_fetch_rejects_an_unknown_series():
    with pytest.raises(ValueError, match="nope"):
        make_source(FakeSession()).fetch("nope", date(2026, 1, 1), date(2026, 1, 2))


def test_fetch_does_not_retry_a_client_error_and_raises():
    # 404 is the portal's answer to a wrong key -- retrying can't change it.
    session = FakeSession(FakeResponse(status_code=404), FakeResponse(text=ESTR_FIRST_DAYS_CSV))

    with pytest.raises(RuntimeError, match="estr"):
        make_source(session).fetch("estr", date(2019, 10, 1), date(2019, 10, 4))

    assert len(session.calls) == 1


def test_fetch_retries_a_server_error_then_succeeds():
    session = FakeSession(FakeResponse(status_code=503), FakeResponse(text=ESTR_FIRST_DAYS_CSV))

    result = make_source(session).fetch("estr", date(2019, 10, 1), date(2019, 10, 4))

    assert len(session.calls) == 2
    assert len(result) == 4


def test_fetch_retries_rate_limiting():
    session = FakeSession(FakeResponse(status_code=429), FakeResponse(text=ESTR_FIRST_DAYS_CSV))

    make_source(session).fetch("estr", date(2019, 10, 1), date(2019, 10, 4))

    assert len(session.calls) == 2


def test_fetch_retries_a_connection_error():
    session = FakeSession(
        requests.ConnectionError("connection reset"), FakeResponse(text=ESTR_FIRST_DAYS_CSV)
    )

    make_source(session).fetch("estr", date(2019, 10, 1), date(2019, 10, 4))

    assert len(session.calls) == 2


def test_fetch_raises_once_retries_are_exhausted():
    session = FakeSession(*[FakeResponse(status_code=503)] * 3)

    with pytest.raises(RuntimeError, match="estr"):
        make_source(session, max_retries=3).fetch("estr", date(2019, 10, 1), date(2019, 10, 4))

    assert len(session.calls) == 3
