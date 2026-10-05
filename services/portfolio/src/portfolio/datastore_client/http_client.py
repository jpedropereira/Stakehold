"""The real DataStoreClient implementation, calling data-store's read API (task 3). See
data-store/docs/architecture.md's "Read API" section for the endpoint contract this depends on.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import requests
from portfolio.datastore_client.base import FxPoint, PricePoint, TickerInfo
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

        try:
            points = response.json()["points"]
        except (ValueError, KeyError) as exc:
            # ValueError covers requests' JSONDecodeError (a json.JSONDecodeError subclass) for
            # a 200 whose body isn't valid JSON at all.
            raise DataStoreApiError(
                f"data-store returned a malformed response for {base}/{quote}: {exc}"
            ) from exc
        if not points:
            return None
        try:
            latest = max(points, key=lambda point: point["date"])
            return Decimal(latest["rate"])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed fx-rate point for {base}/{quote}: {exc}"
            ) from exc

    def get_price_series(self, ticker: str, start: date, end: date) -> list[PricePoint]:
        try:
            response = requests.get(
                f"{self._base_url}/prices/{ticker}",
                params={"start": start.isoformat(), "end": end.isoformat()},
                headers=self._headers,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        # Unlike get_fx_rate's 404 (an untracked currency, a legitimate "no rate" outcome), a
        # 404 here means the ticker isn't ingested at all -- by the time this is called,
        # get_ticker_info has already confirmed it is, so this signals something went wrong,
        # not "no data in range" (which is a normal 200 with empty points).
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for prices/{ticker}: {exc}"
            ) from exc

        try:
            points = response.json()["points"]
        except (ValueError, KeyError) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for prices/{ticker}: {exc}"
            ) from exc

        try:
            return [
                PricePoint(
                    date=date.fromisoformat(point["date"]),
                    split_adjusted_close=Decimal(point["split_adjusted_close"]),
                )
                for point in points
            ]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed price point for {ticker}: {exc}"
            ) from exc

    def get_fx_rate_series(self, base: str, quote: str, start: date, end: date) -> list[FxPoint]:
        try:
            response = requests.get(
                f"{self._base_url}/fx/{base}/{quote}",
                params={"start": start.isoformat(), "end": end.isoformat()},
                headers=self._headers,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        # Matches get_fx_rate's own 404 treatment: an untracked currency is "no rate available",
        # not a failure.
        if response.status_code == 404:
            return []
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for {base}/{quote}: {exc}"
            ) from exc

        try:
            points = response.json()["points"]
        except (ValueError, KeyError) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for {base}/{quote}: {exc}"
            ) from exc

        try:
            return [
                FxPoint(date=date.fromisoformat(point["date"]), rate=Decimal(point["rate"]))
                for point in points
            ]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed fx-rate point for {base}/{quote}: {exc}"
            ) from exc

    def list_tickers(self) -> list[str]:
        try:
            response = requests.get(
                f"{self._base_url}/tickers", headers=self._headers, timeout=_REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for tickers: {exc}"
            ) from exc

        try:
            tickers = response.json()["tickers"]
            if not isinstance(tickers, list) or not all(isinstance(t, str) for t in tickers):
                raise TypeError("tickers is not a list of strings")
            return tickers
        except (ValueError, KeyError, TypeError) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for tickers: {exc}"
            ) from exc

    def get_ticker_info(self, ticker: str) -> TickerInfo:
        try:
            response = requests.get(
                f"{self._base_url}/tickers/{ticker}",
                headers=self._headers,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        # No 404 special-case here, unlike get_price_series/get_fx_rate_series -- this endpoint
        # always answers 200, with exists=False as the answer for an unknown ticker.
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for tickers/{ticker}: {exc}"
            ) from exc

        try:
            body = response.json()
            return TickerInfo(exists=body["exists"], has_returns=body["has_returns"])
        except (ValueError, KeyError, TypeError) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for tickers/{ticker}: {exc}"
            ) from exc
