"""The API's routes: argument parsing plus a call into jobs.py, the same shape cli.py has for
the write flows. Nothing here touches SQL or a repository directly, and nothing here decides
*what* a ticker or currency means -- that lives in read_flows.py, ticker_onboarding.py and the
repositories below them. The one decision made here is which flow exception becomes which status
code (the tracked-ticker routes at the bottom).
"""

import logging
from datetime import date
from uuid import UUID

from data_store import jobs, ticker_onboarding
from data_store.api.models import (
    FxSeries,
    PriceSeries,
    TickerInfo,
    TickerList,
    TickerLookup,
    TrackedTicker,
    TrackedTickerAdded,
    TrackedTickerCreate,
    TrackedTickerList,
    TrackedTickerUpdate,
)
from data_store.api.security import require_internal_secret
from data_store.read_flows import CurrencyNotTracked, TickerNotFound
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

logger = logging.getLogger(__name__)

# Unauthenticated on purpose -- a liveness probe must work before any secret is provisioned.
health_router = APIRouter()


@health_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


router = APIRouter(dependencies=[Depends(require_internal_secret)])


def _validate_range(start: date, end: date) -> None:
    if start > end:
        raise HTTPException(400, "start must not be after end")


@router.get("/prices/{ticker}", response_model=PriceSeries)
def get_prices(
    ticker: str = Path(min_length=1, max_length=20),
    start: date = Query(...),
    end: date = Query(...),
    source: str = Query("yfinance"),
) -> dict:
    _validate_range(start, end)
    logger.info("prices: ticker=%s source=%s %s..%s", ticker, source, start, end)
    try:
        return jobs.get_price_series(ticker=ticker, source=source, start=start, end=end)
    except TickerNotFound:
        raise HTTPException(404, f"ticker not found: {ticker}")


@router.get("/fx/{base}/{quote}", response_model=FxSeries)
def get_fx(
    base: str = Path(pattern=r"^[A-Z]{3}$"),
    quote: str = Path(pattern=r"^[A-Z]{3}$"),
    start: date = Query(...),
    end: date = Query(...),
    source: str = Query("yfinance"),
) -> dict:
    _validate_range(start, end)
    logger.info("fx: %s/%s source=%s %s..%s", base, quote, source, start, end)
    try:
        return jobs.get_fx_series(base=base, quote=quote, source=source, start=start, end=end)
    except CurrencyNotTracked as e:
        raise HTTPException(404, f"currency not tracked: {e}")


@router.get("/tickers", response_model=TickerList)
def list_tickers(source: str = Query("yfinance")) -> dict:
    """Every ingested ticker for `source`. Unpaginated on purpose: ~1.3k tickers is ~10 KB, and
    the one caller (portfolio, for its manual-entry picker) needs the whole set at once."""
    logger.info("ticker list: source=%s", source)
    result = jobs.get_ticker_list(source=source)
    logger.info("ticker list: %d ticker(s) for source=%s", len(result["tickers"]), source)
    return result


@router.get("/tickers/{ticker}", response_model=TickerInfo)
def get_ticker(
    ticker: str = Path(min_length=1, max_length=20),
    source: str = Query("yfinance"),
) -> dict:
    logger.info("ticker info: ticker=%s source=%s", ticker, source)
    return jobs.get_ticker_info(ticker=ticker, source=source)


# --- tracked tickers ------------------------------------------------------------------------------
#
# Every flow exception names an outcome the caller has to tell its user apart; this is the one
# place each becomes a status code. The messages are plain and for logs and API callers -- web
# writes its own copy from the status code.

_TICKER_REQUEST_ERRORS = {
    ticker_onboarding.InvalidSymbol: (422, "not a valid ticker symbol"),
    ticker_onboarding.UnknownLookupSource: (422, "no ticker lookup for this source"),
    ticker_onboarding.CannotBeHeld: (422, "an index or currency pair, which can't be held"),
    ticker_onboarding.NotOnProvider: (
        404,
        "not found on the provider -- delisted securities may not be available",
    ),
    ticker_onboarding.RequestLimitReached: (429, "daily limit of new tickers reached"),
    ticker_onboarding.ProviderUnavailable: (503, "the provider didn't answer; try again later"),
    ticker_onboarding.TrackedTickerNotFound: (404, "not a tracked ticker"),
    ticker_onboarding.ManagedByConfig: (
        409,
        "managed in tracked_tickers.yaml; enable or disable it there",
    ),
    ticker_onboarding.NotRetryable: (409, "only a failed ticker can be retried"),
}


def _ticker_request_error(error: Exception) -> HTTPException:
    status_code, message = _TICKER_REQUEST_ERRORS[type(error)]
    return HTTPException(status_code, f"{message}: {error}")


@router.get("/tickers/{ticker}/lookup", response_model=TickerLookup)
def lookup_ticker(
    ticker: str = Path(min_length=1, max_length=40),
    source: str = Query("yfinance"),
) -> dict:
    logger.info("ticker lookup: ticker=%r source=%s", ticker, source)
    try:
        return jobs.get_ticker_lookup(ticker=ticker, source=source)
    except tuple(_TICKER_REQUEST_ERRORS) as e:
        raise _ticker_request_error(e)


@router.post("/tracked-tickers", response_model=TrackedTickerAdded)
def add_tracked_ticker(body: TrackedTickerCreate, response: Response) -> dict:
    """201 when this call added the ticker, 200 when it was already tracked -- the body is the
    row either way, so the caller can show its status."""
    logger.info(
        "add tracked ticker: ticker=%r requested_by=%s enforce_limit=%s",
        body.ticker,
        body.requested_by,
        body.enforce_limit,
    )
    try:
        result = jobs.run_add_tracked_ticker(
            ticker=body.ticker,
            requested_by=str(body.requested_by),
            enforce_limit=body.enforce_limit,
            source=body.source,
        )
    except tuple(_TICKER_REQUEST_ERRORS) as e:
        raise _ticker_request_error(e)
    response.status_code = 201 if result["created"] else 200
    return result


@router.get("/tracked-tickers", response_model=TrackedTickerList)
def list_tracked_tickers(
    source: str = Query("yfinance"),
    requested_by: UUID | None = Query(None),
    status: list[str] | None = Query(None),
    ticker: list[str] | None = Query(None),
) -> dict:
    return jobs.get_tracked_tickers(
        source=source,
        requested_by=str(requested_by) if requested_by else None,
        statuses=status,
        tickers=ticker,
    )


@router.patch("/tracked-tickers/{ticker}", response_model=TrackedTicker)
def update_tracked_ticker(
    body: TrackedTickerUpdate,
    ticker: str = Path(min_length=1, max_length=20),
    source: str = Query("yfinance"),
) -> dict:
    logger.info(
        "update tracked ticker: ticker=%s source=%s enabled=%s retry=%s",
        ticker,
        source,
        body.enabled,
        body.retry,
    )
    try:
        return jobs.run_update_tracked_ticker(
            ticker=ticker, source=source, enabled=body.enabled, retry=body.retry
        )
    except tuple(_TICKER_REQUEST_ERRORS) as e:
        raise _ticker_request_error(e)
