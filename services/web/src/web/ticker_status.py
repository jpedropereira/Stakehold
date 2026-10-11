"""How a tracked ticker's loading status reads, wherever web shows it: the Tickers pages and a
holding still waiting for prices (ticker onboarding, TK-8/TK-9). One place, so the same status
never reads two ways.

data-store's statuses are "pending" (queued), "backfilling" (loading its history), "ready" and
"failed" (given up on after its retries). Copy follows the charter's voice: plain, no alarm.
"""

import logging
import re

from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import PortfolioClientError

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "pending": "Queued",
    "backfilling": "Loading",
    "ready": "Ready",
    "failed": "Couldn't load",
}

LOADING_STATUSES = frozenset({"pending", "backfilling"})

# What a ticker symbol can look like (data-store's own whitelist). Used before echoing a ticker
# from a query string back onto a page, so a hand-edited URL can't put arbitrary text there.
_TICKER_SHAPE = re.compile(r"^[A-Z0-9.\-=^]{1,20}$")


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def ticker_or_none(value: str | None) -> str | None:
    return value if value and _TICKER_SHAPE.fullmatch(value) else None


def holding_price_note(status: str | None) -> str:
    """The text in place of a holding's figures while it has no returns."""
    if status in LOADING_STATUSES:
        return "Prices loading"
    if status == "failed":
        return "Prices couldn't be loaded"
    return "Waiting for prices"


async def load_statuses(portfolio_client: PortfolioClient, tickers: list[str]) -> dict[str, str]:
    """{ticker: status} for whichever of `tickers` data-store tracks directly. Empty when there's
    nothing to ask about, or when the call fails: a holding then reads "Waiting for prices",
    which is true either way, rather than taking the page down."""
    if not tickers:
        return {}
    try:
        rows = await portfolio_client.list_tracked_tickers(tickers=sorted(tickers))
    except PortfolioClientError as exc:
        logger.warning("portfolio service unavailable listing ticker statuses: %s", exc)
        return {}
    return {row.ticker: row.status for row in rows}
