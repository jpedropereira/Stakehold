"""Each ticker's daily EUR returns for the risk path, or why it has none: fetch.py's fan-out, then
calculator.py's conversion, worded and logged. The portfolio TWR (RK-10) and ex-ante risk
(RK-17) both need exactly this, so it was lifted out of twr/flows.py rather than copied.

fetch.py and calculator.py neither word nor log, because Labs words the same statuses its own
way ("no FX rate"). This module is the risk path's wording:

- "not tracked", "no history yet", "couldn't reach data-store" (the exception goes to the log:
  it names internal URLs, and the user can only act on "try again later");
- "no FX series for <CCY>" when the currency has no rates, and "no FX series for its currency"
  when data-store couldn't map the ticker to a currency at all.

Every ticker gets one log line on the caller's `logger`, prefixed with its `context`: included,
with its day count, or left out with its reason. The caller's logger, so each line stays under the
flow it belongs to.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from portfolio.datastore_client.base import DataStoreClient
from portfolio.eur_returns import fetch
from portfolio.eur_returns.calculator import NO_FX_SERIES, EurReturns, eur_daily_returns

REASONS = {
    fetch.NOT_TRACKED: "not tracked",
    fetch.NO_HISTORY: "no history yet",
    fetch.NO_FX_RATE: "no FX series for its currency",
    fetch.FAILED: "couldn't reach data-store",
}


@dataclass(frozen=True)
class ExcludedHolding:
    ticker: str
    status: str  # not_tracked, no_history, no_fx_rate or failed
    reason: str


@dataclass(frozen=True)
class EligibleReturns:
    returns: dict[str, EurReturns]  # the included tickers, in input order
    currencies: dict[str, str]  # each included ticker's FX currency
    excluded: list[ExcludedHolding]  # in input order


def fetch_eur_returns(
    datastore_client: DataStoreClient,
    tickers: Sequence[str],
    start: date | Mapping[str, date],
    end: date,
    *,
    logger: logging.Logger,
    context: str,
) -> EligibleReturns:
    """`tickers` (unique) from `start` (one date, or one per ticker) to `end`."""
    returns: dict[str, EurReturns] = {}
    currencies: dict[str, str] = {}
    excluded = []
    for ticker, outcome in fetch.fetch_series(datastore_client, tickers, start, end).items():
        if isinstance(outcome, fetch.LeftOut):
            reason = REASONS[outcome.status]
            if outcome.status == fetch.FAILED:
                logger.error(
                    "%s: %s: failed, left out -- a data-store call raised: %s",
                    context,
                    ticker,
                    outcome.exc,
                    exc_info=outcome.exc,
                )
            else:
                logger.warning("%s: %s: left out, %s.", context, ticker, reason)
            excluded.append(ExcludedHolding(ticker, outcome.status, reason))
            continue

        series = eur_daily_returns(outcome.prices, outcome.fx)
        if isinstance(series, EurReturns):
            logger.info(
                "%s: %s: included, %d day(s) of EUR returns.", context, ticker, len(series.dates)
            )
            returns[ticker] = series
            currencies[ticker] = outcome.info.fx_currency
            continue
        if series.reason == NO_FX_SERIES:
            status, reason = fetch.NO_FX_RATE, f"no FX series for {outcome.info.fx_currency}"
        else:
            status, reason = fetch.NO_HISTORY, REASONS[fetch.NO_HISTORY]
        logger.warning("%s: %s: left out, %s.", context, ticker, reason)
        excluded.append(ExcludedHolding(ticker, status, reason))
    return EligibleReturns(returns, currencies, excluded)
