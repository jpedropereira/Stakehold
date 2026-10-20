"""Provider-agnostic transient-error classifier and retry loop, for the HTTP providers that
aren't yfinance (FRED, Eurostat, the ECB).

Kept apart from `yfinance_retry.py` because that classifier matches yfinance's specific error
signatures, and these providers' errors share none of them. It started as `macro/retry.py`,
while FRED and Eurostat were its only callers. It became a foundation module when `risk_free/`
needed it too, because one domain importing another's helper is the shape `yfinance_retry.py`
was promoted to avoid.
"""

import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


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


def with_retries(
    fn: Callable[[], T],
    *,
    description: str,
    log_prefix: str,
    max_retries: int,
    backoff_base_seconds: float,
) -> T | None:
    """Runs `fn()`, retrying on a transient error with exponential backoff + jitter. Never
    raises -- a permanently-failing call is logged and `None` is returned, so one bad series or
    dataset can't cost the others in the caller's own fetch loop. `description` and `log_prefix`
    (e.g. a series/dataset id and "fred"/"eurostat") are for the warning log lines only.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as e:
            if is_transient(e) and attempt < max_retries:
                sleep_seconds = backoff_base_seconds * (2 ** (attempt - 1))
                sleep_seconds += random.uniform(0, 0.25)
                logger.warning(
                    "%s: transient error on attempt %d/%d for %s: %s. Retrying in %.2fs.",
                    log_prefix,
                    attempt,
                    max_retries,
                    description,
                    e,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue
            logger.warning(
                "%s: %s failed after %d attempt(s), skipping: %s",
                log_prefix,
                description,
                attempt,
                e,
                exc_info=True,
            )
            return None
