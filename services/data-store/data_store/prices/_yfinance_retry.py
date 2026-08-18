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
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)
