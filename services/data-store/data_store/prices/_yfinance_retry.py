from datetime import date

_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "connection aborted",
    "connection reset",
    "remote disconnected",
    "temporarily unavailable",
    "try again",
    "429",
    "too many requests",
    "rate limit",
    # urllib3/requests and curl_cffi phrasing for a connection that never got established.
    # These are the most common real-world failures and none of the markers above match them.
    "max retries exceeded",
    "could not resolve host",
    "couldn't connect to server",
    "failed to connect",
)


def _batch_size(start_date: date, end_date: date) -> int:
    days = (end_date - start_date).days
    if days <= 90:
        return 50
    if days <= 365:
        return 20
    if days <= 5 * 365:
        return 10
    return 5


def _batches(tickers: list[str], batch_size: int):
    for i in range(0, len(tickers), batch_size):
        yield tickers[i : i + batch_size]


def _is_transient(exc: Exception) -> bool:
    # OSError, not (TimeoutError, ConnectionError): neither requests' nor curl_cffi's
    # ConnectionError subclasses the *builtin* one -- both go
    # ConnectionError -> RequestException -> OSError -- so an isinstance check against the
    # builtin silently misses the exact failures yfinance raises through (verified against the
    # installed requests/curl_cffi). Everything reaching here came out of a network call, so
    # OSError is the right breadth; genuine programming errors (ValueError, KeyError, TypeError)
    # aren't OSErrors and still fall through to the marker check, which won't match them either.
    if isinstance(exc, OSError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)
