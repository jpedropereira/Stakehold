"""The full names behind the tickers a page shows (UX-1), for the `ticker` macro's tooltip
(partials/_ticker.html) and the pickers' options. data-store keeps a display_name for every
ticker it tracks: the name in its YAML list, or the provider's long name.

One unfiltered call per page (UX-2), not one filtered to the page's tickers. The pickers offer
every priced ticker (~1.3k, most of them fund constituents), too many for one query string, and
only tracked tickers have a name at all: the YAML's handful plus what users have requested, which
a daily limit caps. So the whole tracked list is small, and one map covers a page's pickers,
tables and prose without each route collecting its tickers from nested view models. If that list
ever grows to thousands, go back to asking for the page's own tickers.

A name is cosmetic: the ticker alone still identifies the instrument. So a failed lookup leaves
the page with bare tickers rather than taking it down.
"""

import logging
from collections.abc import Iterable

from web.portfolio_client.base import PortfolioClient, TrackedTicker
from web.portfolio_client.errors import PortfolioClientError

logger = logging.getLogger(__name__)


async def load_tracked(portfolio_client: PortfolioClient) -> list[TrackedTicker]:
    """Every tracked ticker, from one call. Empty when the call fails. The holdings pages read
    both names and loading statuses (ticker_status.statuses_from) from these rows."""
    try:
        return await portfolio_client.list_tracked_tickers()
    except PortfolioClientError as exc:
        logger.warning("portfolio service unavailable listing tracked tickers: %s", exc)
        return []


def names_from(rows: Iterable[TrackedTicker]) -> dict[str, str]:
    """{ticker: display_name}. A name that is empty or only repeats its ticker is left out, since
    it would add nothing."""
    names = {}
    for row in rows:
        name = (row.display_name or "").strip()
        if name and name != row.ticker:
            names[row.ticker] = name
    return names


async def load_names(portfolio_client: PortfolioClient) -> dict[str, str]:
    """{ticker: display_name} for every tracked ticker. Empty when the call fails."""
    return names_from(await load_tracked(portfolio_client))
