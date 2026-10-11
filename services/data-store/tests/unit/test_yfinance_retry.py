from types import SimpleNamespace

import pytest
import requests
from data_store.yfinance_retry import _is_missing_symbol, _is_transient, _is_unreachable
from yfinance.exceptions import YFPricesMissingError, YFTickerMissingError


class FakeCurlError(OSError):
    """Stands in for curl_cffi.requests.exceptions.ConnectionError, which yfinance raises
    through. Like requests' own ConnectionError, it derives from OSError but *not* from the
    builtin ConnectionError -- which is what an isinstance check on the builtin misses."""


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(
            requests.exceptions.ConnectionError(
                "HTTPSConnectionPool(host='query2.finance.yahoo.com', port=443): "
                "Max retries exceeded with url: /v8/finance/chart/AAPL"
            ),
            id="requests-connection-error",
        ),
        pytest.param(
            FakeCurlError("Failed to perform, curl: (6) Could not resolve host: query2.yahoo.com"),
            id="curl-cffi-could-not-resolve-host",
        ),
        pytest.param(
            FakeCurlError("Failed to perform, curl: (7) Couldn't connect to server"),
            id="curl-cffi-connect-failed",
        ),
        pytest.param(TimeoutError("timed out"), id="builtin-timeout"),
        pytest.param(ConnectionResetError("connection reset by peer"), id="builtin-conn-reset"),
    ],
)
def test_real_network_failures_are_transient(exc):
    assert _is_transient(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(ValueError("No tickers provided."), id="programming-error"),
        pytest.param(KeyError("Adj Close"), id="unexpected-response-shape"),
        pytest.param(TypeError("unsupported operand"), id="type-error"),
    ],
)
def test_programming_errors_are_not_transient(exc):
    # Retrying these just delays a failure that will never fix itself.
    assert _is_transient(exc) is False


def test_rate_limiting_is_transient():
    # yfinance's YFRateLimitError surfaces as this text; it's the failure the backoff exists for.
    assert _is_transient(Exception("Too Many Requests. Rate limited. Try after a while.")) is True


class FakeHttpError(OSError):
    """Shaped like curl_cffi's HTTPError: an OSError carrying the response."""

    def __init__(self, status_code):
        super().__init__(f"HTTP Error {status_code}: ")
        self.response = SimpleNamespace(status_code=status_code)


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(FakeHttpError(404), id="http-404"),
        pytest.param(YFTickerMissingError("GHOST", "no timezone found"), id="ticker-missing"),
        pytest.param(YFPricesMissingError("GHOST", ""), id="prices-missing"),
    ],
)
def test_the_provider_saying_a_symbol_doesnt_exist_is_a_missing_symbol(exc):
    assert _is_missing_symbol(exc) is True
    # ...and never "unreachable", although a 404 is an OSError and so counts as transient.
    assert _is_unreachable(exc) is False


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(
            FakeCurlError("Failed to perform, curl: (6) Could not resolve host: query2.yahoo.com"),
            id="dns-failure",
        ),
        pytest.param(FakeHttpError(503), id="http-503"),
        pytest.param(TimeoutError("timed out"), id="timeout"),
        pytest.param(Exception("Too Many Requests. Rate limited."), id="rate-limited"),
    ],
)
def test_a_provider_that_didnt_answer_is_unreachable(exc):
    assert _is_missing_symbol(exc) is False
    assert _is_unreachable(exc) is True


def test_a_programming_error_is_neither():
    assert _is_missing_symbol(ValueError("bad")) is False
    assert _is_unreachable(ValueError("bad")) is False
