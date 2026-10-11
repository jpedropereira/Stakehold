from yfinance.exceptions import YFTickerMissingError

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


def _is_missing_symbol(exc: Exception) -> bool:
    """The provider answered, and said this symbol doesn't exist: an HTTP 404 (an unknown or
    long-delisted symbol, verified in TK-0) or yfinance's YFTickerMissingError family. Check
    this before _is_transient where the difference matters -- a 404 is an OSError, so
    _is_transient alone counts it as a network failure."""
    if isinstance(exc, YFTickerMissingError):
        return True
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 404


def _is_unreachable(exc: Exception) -> bool:
    """The provider didn't answer (a network failure, a timeout, rate limiting), as opposed to
    answering that it has nothing for this symbol."""
    return _is_transient(exc) and not _is_missing_symbol(exc)
