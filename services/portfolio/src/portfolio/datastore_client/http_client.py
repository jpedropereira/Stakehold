"""The real DataStoreClient implementation, calling data-store's read API (task 3). See
data-store/docs/read-api.md for the endpoint contract this depends on.
"""

from dataclasses import fields, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from uuid import UUID

import requests
from portfolio.datastore_client.base import (
    EtfConstituent,
    EtfFund,
    EtfHoldings,
    FxPoint,
    PricePoint,
    Regime,
    RegimeList,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
    TickerLookup,
    TrackedTicker,
)
from portfolio.errors import DataStoreApiError, TickerRequestRefused

_REQUEST_TIMEOUT = 10  # seconds
# Covers a long weekend or a bank holiday with no traded rate; GET /fx/{base}/{quote} returns
# every point actually stored in the window, so a wider window than strictly needed is harmless.
_LOOKBACK_DAYS = 7
# data-store's tracked-ticker refusals that carry a meaning for the user. Anything else
# unexpected stays a DataStoreApiError (502), like every other call here.
_REFUSAL_STATUSES = frozenset({404, 409, 422, 429, 503})
# A lookup or add can wait on one provider call inside data-store (its own timeout is 10 s).
_TRACKED_TICKER_TIMEOUT = 20  # seconds


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
                f"{self._base_url}/prices/{_path(ticker)}",
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

        # simple_return is required even though it's nullable: data-store always sends the key,
        # and defaulting a missing one to None would turn a contract break into silent gaps.
        try:
            return [
                PricePoint(
                    date=date.fromisoformat(point["date"]),
                    split_adjusted_close=Decimal(point["split_adjusted_close"]),
                    simple_return=_optional_decimal(point["simple_return"]),
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

    def get_risk_free_series(self, start: date, end: date) -> RiskFreeSeries | None:
        # source is left to data-store's default (ecb), as the other reads leave theirs.
        try:
            response = requests.get(
                f"{self._base_url}/rates/risk-free",
                params={"start": start.isoformat(), "end": end.isoformat()},
                headers=self._headers,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        # No 404 special-case: "nothing stored" is a 200 with first_date null, handled below.
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for rates/risk-free: {exc}"
            ) from exc

        try:
            body = response.json()
            first_date = body["first_date"]
            points = [
                RiskFreePoint(
                    date=date.fromisoformat(point["date"]),
                    rate=Decimal(point["rate"]),
                    series=point["series"],
                )
                for point in body["points"]
            ]
            if first_date is None:
                # data-store's "nothing stored" -- None here, so no caller can take it for rf = 0.
                if points:
                    raise ValueError("points without a first_date")
                return None
            return RiskFreeSeries(first_date=date.fromisoformat(first_date), points=points)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for rates/risk-free: {exc}"
            ) from exc

    def get_regimes(self, start: date, end: date) -> RegimeList:
        try:
            response = requests.get(
                f"{self._base_url}/regimes",
                params={"start": start.isoformat(), "end": end.isoformat()},
                headers=self._headers,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for regimes: {exc}"
            ) from exc

        try:
            body = response.json()
            return RegimeList(
                total_regimes=int(body["total_regimes"]),
                regimes=[
                    Regime(
                        regime_id=row["regime_id"],
                        dimension=row["dimension"],
                        scope=row["scope"],
                        name=row["name"],
                        start_date=date.fromisoformat(row["start_date"]),
                        end_date=_optional_date(row["end_date"]),
                        known_from=_optional_date(row["known_from"]),
                        severity=row["severity"],
                        source=row["source"],
                        notes=row["notes"],
                    )
                    for row in body["regimes"]
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for regimes: {exc}"
            ) from exc

    def list_etf_funds(self) -> list[EtfFund]:
        body = self._get_json("/etf-holdings", what="etf-holdings")
        try:
            funds = body["funds"]
            if not isinstance(funds, list):
                raise TypeError("funds is not a list")
            parsed = [_parse(EtfFund, fund, what="ETF fund") for fund in funds]
            return [
                EtfFund(
                    fund_key=fund.fund_key,
                    display_name=fund.display_name,
                    ticker=fund.ticker,
                    enabled=fund.enabled,
                    latest_as_of_date=(
                        None
                        if fund.latest_as_of_date is None
                        else date.fromisoformat(fund.latest_as_of_date)
                    ),
                )
                for fund in parsed
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for etf-holdings: {exc}"
            ) from exc

    def get_latest_etf_holdings(self, fund_key: str) -> EtfHoldings | None:
        path = f"/etf-holdings/{_path(fund_key)}/latest"
        # Both of data-store's 404s (a fund outside etfs.yaml, a fund with no snapshot) mean the
        # same here: there is nothing to look through.
        body = self._get_json(path, what=f"etf-holdings/{fund_key}/latest", none_on_404=True)
        if body is None:
            return None
        try:
            constituents = []
            for line in body["constituents"]:
                row = _parse(EtfConstituent, line, what="ETF constituent")
                constituents.append(
                    replace(row, weight_percent=_optional_decimal(row.weight_percent))
                )
            return EtfHoldings(
                fund_key=body["fund_key"],
                ticker=body["ticker"],
                as_of_date=date.fromisoformat(body["as_of_date"]),
                constituents=constituents,
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for {path}: {exc}"
            ) from exc

    def _get_json(self, path: str, *, what: str, none_on_404: bool = False):
        try:
            response = requests.get(
                f"{self._base_url}{path}", headers=self._headers, timeout=_REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc
        if none_on_404 and response.status_code == 404:
            return None
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DataStoreApiError(
                f"data-store returned {response.status_code} for {what}: {exc}"
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise DataStoreApiError(f"data-store returned a malformed response for {what}") from exc

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
                f"{self._base_url}/tickers/{_path(ticker)}",
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
            # A missing currency only leaves an asset without an FX pair, which a caller reports;
            # it can't produce a wrong figure, so absent reads as unknown rather than malformed.
            return TickerInfo(
                exists=body["exists"],
                has_returns=body["has_returns"],
                status=body.get("status"),
                currency=body.get("currency"),
                fx_currency=body.get("fx_currency"),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise DataStoreApiError(
                f"data-store returned a malformed response for tickers/{ticker}: {exc}"
            ) from exc

    # --- tracked tickers ----------------------------------------------------------------------

    def lookup_ticker(self, ticker: str) -> TickerLookup:
        body = self._tracked_request("GET", f"/tickers/{_path(ticker)}/lookup")
        return _parse(TickerLookup, body, what="ticker lookup")

    def add_tracked_ticker(
        self, ticker: str, *, requested_by: UUID, enforce_limit: bool
    ) -> tuple[TrackedTicker, bool]:
        body = self._tracked_request(
            "POST",
            "/tracked-tickers",
            json={
                "ticker": ticker,
                "requested_by": str(requested_by),
                "enforce_limit": enforce_limit,
            },
        )
        try:
            created = body["created"]
        except (KeyError, TypeError) as exc:
            raise DataStoreApiError(f"data-store returned a malformed tracked ticker: {exc}")
        return _parse(TrackedTicker, body, what="tracked ticker"), bool(created)

    def list_tracked_tickers(
        self,
        *,
        requested_by: UUID | None = None,
        statuses: list[str] | None = None,
        tickers: list[str] | None = None,
    ) -> list[TrackedTicker]:
        params = []
        if requested_by is not None:
            params.append(("requested_by", str(requested_by)))
        params.extend(("status", status) for status in statuses or [])
        params.extend(("ticker", ticker) for ticker in tickers or [])
        body = self._tracked_request("GET", "/tracked-tickers", params=params)
        try:
            rows = body["tracked_tickers"]
            return [_parse(TrackedTicker, row, what="tracked ticker") for row in rows]
        except (KeyError, TypeError) as exc:
            raise DataStoreApiError(f"data-store returned a malformed tracked-ticker list: {exc}")

    def update_tracked_ticker(
        self, ticker: str, *, enabled: bool | None, retry: bool
    ) -> TrackedTicker:
        body = self._tracked_request(
            "PATCH",
            f"/tracked-tickers/{_path(ticker)}",
            json={"enabled": enabled, "retry": retry},
        )
        return _parse(TrackedTicker, body, what="tracked ticker")

    def _tracked_request(self, method: str, path: str, **kwargs) -> dict:
        """One tracked-ticker call. A refusal the user must hear about keeps data-store's status
        code (TickerRequestRefused); anything else unexpected is a DataStoreApiError."""
        try:
            response = requests.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                timeout=_TRACKED_TICKER_TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise DataStoreApiError(f"request to data-store failed: {exc}") from exc

        try:
            body = response.json()
        except ValueError:
            body = None

        if response.status_code in _REFUSAL_STATUSES:
            detail = body.get("detail") if isinstance(body, dict) else None
            raise TickerRequestRefused(
                str(detail or f"data-store refused {method} {path}"), response.status_code
            )
        if response.status_code >= 400:
            raise DataStoreApiError(f"data-store returned {response.status_code} for {path}")
        if not isinstance(body, dict):
            raise DataStoreApiError(f"data-store returned a malformed response for {path}")
        return body


def _optional_decimal(value) -> Decimal | None:
    """A nullable numeric field: None stays None (a gap), anything else must parse."""
    return None if value is None else Decimal(value)


def _optional_date(value) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _path(segment: str) -> str:
    """A user-typed ticker, quoted so it stays one path segment ("a/b" can't reach another
    route). data-store whitelists the symbol itself; this only keeps the URL what it says."""
    return quote(segment, safe="")


def _parse(cls, body, *, what: str):
    try:
        return cls(**{field.name: body[field.name] for field in fields(cls)})
    except (KeyError, TypeError) as exc:
        raise DataStoreApiError(f"data-store returned a malformed {what}: {exc}") from exc
