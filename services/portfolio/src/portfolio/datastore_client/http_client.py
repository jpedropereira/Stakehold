"""The real DataStoreClient implementation, calling data-store's read API (task 3). See
data-store/docs/architecture.md's "Read API" section for the endpoint contract this depends on.
"""

from datetime import date, timedelta
from decimal import Decimal

import requests
from portfolio.errors import DataStoreApiError

_REQUEST_TIMEOUT = 10  # seconds
# Covers a long weekend or a bank holiday with no traded rate; GET /fx/{base}/{quote} returns
# every point actually stored in the window, so a wider window than strictly needed is harmless.
_LOOKBACK_DAYS = 7


class HttpDataStoreClient:
    def __init__(self, base_url: str, internal_api_secret: str):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {internal_api_secret}"}

    def get_fx_rate(self, base: str, quote: str, on_date: date) -> Decimal | None:
        start = on_date - timedelta(days=_LOOKBACK_DAYS)
        try:
            response = requests.get(
                f"{self._base_url}/fx/{base}/{quote}",
                params={"start": start.isoformat(), "end": on_date.isoformat()},
                headers=self._headers,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        # A 404 here means the currency isn't tracked at all (data-store's CurrencyNotTracked) --
        # treated the same as "no points in range", not a distinct failure mode a caller needs
        # to react to differently.
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for {base}/{quote}: {exc}"
            ) from exc

        points = response.json()["points"]
        if not points:
            return None
        latest = max(points, key=lambda point: point["date"])
        return Decimal(latest["rate"])
