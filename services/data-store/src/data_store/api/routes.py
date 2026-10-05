"""The read API's routes: argument parsing plus a call into jobs.py, the same shape cli.py has
for the write flows. Nothing here touches SQL or a repository directly, and nothing here decides
*what* a ticker or currency means -- that lives in read_flows.py and the repositories below it.
"""

import logging
from datetime import date

from data_store import jobs
from data_store.api.models import FxSeries, PriceSeries, TickerInfo, TickerList
from data_store.api.security import require_internal_secret
from data_store.read_flows import CurrencyNotTracked, TickerNotFound
from fastapi import APIRouter, Depends, HTTPException, Path, Query

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
