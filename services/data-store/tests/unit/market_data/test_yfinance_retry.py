import pytest
import requests
from data_store.market_data._yfinance_retry import _is_transient


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
