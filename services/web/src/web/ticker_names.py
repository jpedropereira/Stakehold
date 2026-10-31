"""The full names behind the tickers a page shows (UX-1), for the `ticker` macro's tooltip
(partials/_ticker.html). data-store keeps a display_name for every ticker it tracks: the name in
its YAML list, or the provider's long name.

A name is cosmetic: the ticker alone still identifies the instrument. So a failed lookup leaves
the page with bare tickers rather than taking it down, as ticker_status.load_statuses does.
"""

import logging
from collections.abc import Iterable

from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import PortfolioClientError

logger = logging.getLogger(__name__)


async def load_names(portfolio_client: PortfolioClient, tickers: Iterable[str]) -> dict[str, str]:
    """{ticker: display_name} for whichever of `tickers` data-store tracks, from one call. A name
    that is empty or only repeats its ticker is left out, since it would add nothing. Empty when
    there's nothing to ask about, or when the call fails."""
    wanted = sorted(set(tickers))
    if not wanted:
        return {}
    try:
        rows = await portfolio_client.list_tracked_tickers(tickers=wanted)
    except PortfolioClientError as exc:
        logger.warning("portfolio service unavailable listing ticker names: %s", exc)
        return {}
    names = {}
    for row in rows:
        name = (row.display_name or "").strip()
        if name and name != row.ticker:
            names[row.ticker] = name
    return names
