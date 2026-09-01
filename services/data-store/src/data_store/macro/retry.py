"""Transient-error classifier shared by fred_source.py and eurostat_source.py.

Scoped to this domain rather than promoted to a foundation module like `yfinance_retry.py`:
that classifier is shared across two different *domains* (market_data/, fx/) that both talk to
yfinance; here both callers are within macro/ itself, talking to two different providers whose
errors don't share yfinance's specific signatures, so a fresh, provider-agnostic classifier
matching *these* two providers' error shapes belongs next to them instead.
"""


def is_transient(e: Exception) -> bool:
    """fredapi wraps every HTTP-layer failure (auth, rate limit, 5xx) into a bare ValueError
    carrying the API's own error text (confirmed against fredapi's `Fred.__fetch_data`, which
    catches `HTTPError` and re-raises `ValueError(root.get("message"))`) -- so there is no
    reliable exception *type* to distinguish "worth retrying" from "will fail identically every
    time" (e.g. an unknown series id also raises plain ValueError). The `eurostat` package and
    the underlying `requests`/`urllib` calls it and fredapi both make can also raise genuine
    connection/timeout errors as their own exception types. Message-substring matching plus a
    broad OSError/TimeoutError check is therefore the only signal available across both
    providers, mirroring the spirit of `yfinance_retry.py`'s classifier without assuming either
    provider's exact exception type.
    """
    if isinstance(e, (OSError, TimeoutError)):
        return True
    message = str(e).lower()
    return any(
        token in message
        for token in (
            "429",
            "rate limit",
            "too many requests",
            "timeout",
            "timed out",
            "connection",
        )
    )
