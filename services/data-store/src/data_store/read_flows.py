"""Read-side orchestration for the HTTP API.

A sibling of `pipeline.py`, not an addition to it: `pipeline.py` is already close to the
context-management file-size guidance and is entirely about write flows, while FX cross-rate
derivation is a read-side decision with no write-flow analogue. Like `pipeline.py`, this module
never touches SQL directly -- reads go through `returns/repository.py` and `fx/repository.py`,
imported as modules (not `from ... import fn`) so a test can monkeypatch the whole repository
namespace the way `jobs.py`'s tests already do for `pipeline` functions.
"""

from datetime import date
from decimal import Decimal

from data_store.config_loader import CurrenciesConfig
from data_store.fx import repository as fx_repository
from data_store.returns import repository as returns_repository

_PRICE_POINT_FIELDS = (
    "date",
    "split_adjusted_close",
    "future_split_factor",
    "price_return",
    "dividend_return",
    "simple_return",
    "log_return",
)


class TickerNotFound(Exception):
    """No row for this (ticker, source) exists in raw_market_data at all -- a typo or an
    untracked ticker, distinct from a valid ticker whose requested date range is simply empty."""


class CurrencyNotTracked(Exception):
    """The requested currency is not one of config/currencies.yaml's enabled codes."""


def price_series(conn, ticker: str, source: str, start: date, end: date) -> dict:
    if not returns_repository.ticker_exists(conn, ticker, source):
        raise TickerNotFound(ticker)

    rows = returns_repository.get_return_series(conn, ticker, source, start, end)
    return {
        "ticker": ticker,
        "source": source,
        "points": [dict(zip(_PRICE_POINT_FIELDS, row)) for row in rows],
    }


def ticker_info(conn, ticker: str, source: str) -> dict:
    exists = returns_repository.ticker_exists(conn, ticker, source)
    first_date, last_date = returns_repository.get_returns_date_range(conn, ticker, source)
    return {
        "ticker": ticker,
        "exists": exists,
        "has_returns": first_date is not None,
        "first_date": first_date,
        "last_date": last_date,
    }


def fx_rate_series(
    conn,
    currencies_config: CurrenciesConfig,
    base: str,
    quote: str,
    source: str,
    start: date,
    end: date,
) -> dict:
    """Any (base, quote) pair among the deployment's enabled currencies, derived from whatever
    fx_rates actually stores -- only (configured_base, X) pairs, per schema.md. Direct and
    inverse cover the common case (either side is the configured base); triangulation covers
    the rest by dividing through it, the same identity schema.md documents for cross-rates."""
    tracked = {c.code for c in currencies_config.currencies if c.enabled}
    if base not in tracked:
        raise CurrencyNotTracked(base)
    if quote not in tracked:
        raise CurrencyNotTracked(quote)

    config_base = currencies_config.base_currency

    if base == quote:
        dates = fx_repository.get_dates_with_rate(conn, base, source, start, end)
        return _fx_result(base, quote, source, "identity", None, [(d, Decimal(1)) for d in dates])

    if base == config_base:
        rows = fx_repository.get_rate_series(conn, base, quote, source, start, end)
        return _fx_result(base, quote, source, "direct", None, rows)

    if quote == config_base:
        rows = fx_repository.get_rate_series(conn, quote, base, source, start, end)
        inverted = [(d, Decimal(1) / rate) for d, rate in rows]
        return _fx_result(base, quote, source, "inverse", None, inverted)

    numerator = dict(fx_repository.get_rate_series(conn, config_base, quote, source, start, end))
    denominator = dict(fx_repository.get_rate_series(conn, config_base, base, source, start, end))
    shared_dates = sorted(set(numerator) & set(denominator))
    triangulated = [(d, numerator[d] / denominator[d]) for d in shared_dates]
    return _fx_result(base, quote, source, "triangulated", config_base, triangulated)


def _fx_result(base, quote, source, derivation, via, rows) -> dict:
    return {
        "base_currency": base,
        "quote_currency": quote,
        "source": source,
        "derivation": derivation,
        "via": via,
        "points": [{"date": d, "rate": rate} for d, rate in rows],
    }
