"""The DataStoreClient Protocol -- portfolio's domain code depends only on this, not on
http_client.py's implementation, so a stub can stand in for tests without a live data-store
instance. See docs/architecture.md's cross-service prerequisite section.
"""

from datetime import date
from decimal import Decimal
from typing import Protocol


class DataStoreClient(Protocol):
    def get_fx_rate(self, base: str, quote: str, on_date: date) -> Decimal | None:
        """The exchange rate for on_date, in transactions/base.py's to_eur convention: quote
        units per 1 base unit (so base="EUR" gives exactly the fx_rate a non-EUR
        CanonicalTransaction needs). Falls back to the most recent earlier date with a rate when
        on_date itself has none (weekends/holidays); returns None when no rate is available at
        all -- an untracked currency, or nothing in the lookback window -- rather than raising,
        since "no rate available" is a normal outcome a caller must decide how to handle, not a
        failure of the call itself.
        """
        ...
