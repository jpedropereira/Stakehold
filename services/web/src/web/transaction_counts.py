"""How many transactions each account holds, for an account delete's warning.

Its own module so routes.py (the accounts list) and api/deletion.py (the confirmation page) share
it without importing each other, like ticker_status.py. portfolio has no count endpoint, so each
account's log is fetched, concurrently, and counted.
"""

import asyncio
import logging

from web.portfolio_client.base import PortfolioClient
from web.portfolio_client.errors import PortfolioClientError

logger = logging.getLogger(__name__)


async def load_transaction_counts(
    portfolio_client: PortfolioClient, user_id: str, account_labels: list[str]
) -> dict[str, int | None]:
    """{label: count}. A count that couldn't be loaded is None, never 0, so the warning can't
    claim an account is empty when it may not be."""
    results = await asyncio.gather(
        *(
            portfolio_client.list_transactions(user_id=user_id, account_label=label)
            for label in account_labels
        ),
        return_exceptions=True,
    )
    counts: dict[str, int | None] = {}
    for label, result in zip(account_labels, results):
        if isinstance(result, PortfolioClientError):
            logger.warning("couldn't count the transactions of account %r: %s", label, result)
            counts[label] = None
        elif isinstance(result, BaseException):
            raise result
        else:
            counts[label] = len(result)
    return counts
