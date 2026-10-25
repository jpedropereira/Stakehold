"""Read-side orchestration for the HTTP API.

A sibling of `pipeline.py`, not an addition to it: `pipeline.py` is already close to the
context-management file-size guidance and is entirely about write flows, while FX cross-rate
derivation is a read-side decision with no write-flow analogue. Like `pipeline.py`, this module
never touches SQL directly -- reads go through the domain repositories (`returns`, `fx`,
`holdings`, `tracked_tickers`, `risk_free`, `regimes`), imported as modules (not
`from ... import fn`) so a test can monkeypatch the whole repository namespace the way `jobs.py`'s
tests already do for `pipeline` functions.
"""

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from data_store.config_loader import CurrenciesConfig, FundConfig, TickerResolution
from data_store.fx import repository as fx_repository
from data_store.holdings import repository as holdings_repository
from data_store.regimes import repository as regimes_repository
from data_store.returns import repository as returns_repository
from data_store.risk_free import repository as risk_free_repository
from data_store.risk_free.splice import EONIA_ADJUSTED, ESTR, splice, spliced_first_date
from data_store.ticker_onboarding import major_currency
from data_store.ticker_resolution import holding_symbol, resolve_yfinance_ticker
from data_store.tracked_tickers import repository as tracked_tickers_repository

logger = logging.getLogger(__name__)

# fx_rates.rate is NUMERIC(18,8), so a stored rate arrives at exactly this scale. A derived one
# is a Decimal division, which would otherwise carry the default context's 28 significant
# digits -- making /fx/GBP/CHF far more precise-looking than /fx/EUR/USD for no reason, since
# the inputs only ever had 8dp of truth in them. Quantizing puts every rate on one scale.
_FX_RATE_SCALE = Decimal("1E-8")

_PRICE_POINT_FIELDS = (
    "date",
    "split_adjusted_close",
    "future_split_factor",
    "price_return",
    "dividend_return",
    "simple_return",
    "log_return",
    "volume",
)


class TickerNotFound(Exception):
    """No row for this (ticker, source) exists in raw_market_data at all -- a typo or an
    untracked ticker, distinct from a valid ticker whose requested date range is simply empty."""


class CurrencyNotTracked(Exception):
    """The requested currency is not one of config/currencies.yaml's enabled codes."""


class FundNotConfigured(Exception):
    """The requested fund_key is not a fund in config/etfs.yaml -- most likely a typo."""


class NoHoldingsSnapshot(Exception):
    """The fund is configured, but no holdings snapshot of it has been stored."""


def price_series(conn, ticker: str, source: str, start: date, end: date) -> dict:
    if not returns_repository.ticker_exists(conn, ticker, source):
        raise TickerNotFound(ticker)

    rows = returns_repository.get_return_series(conn, ticker, source, start, end)
    return {
        "ticker": ticker,
        "source": source,
        "points": [dict(zip(_PRICE_POINT_FIELDS, row)) for row in rows],
    }


def ticker_info(conn, ticker: str, source: str, resolution: TickerResolution) -> dict:
    """`status` is the ticker's onboarding status when it's in tracked_tickers, else None (a
    ticker priced only through a fund's holdings, or not at all). It is reported alongside
    `exists`, not folded into it: a `pending` ticker is tracked but has no stored rows yet.

    `currency` is the provider's spelling, verbatim (`GBp` stays `GBp`): tracked_tickers' when
    it has one, else the newest fund holding that resolves to this ticker, else None.
    `fx_currency` is the code fx_rates is keyed by (`major_currency`), so a caller picks its FX
    pair from it rather than keeping its own copy of the minor-unit map."""
    exists = returns_repository.ticker_exists(conn, ticker, source)
    first_date, last_date = returns_repository.get_returns_date_range(conn, ticker, source)
    tracked = tracked_tickers_repository.get_tracked_ticker(conn, ticker, source)

    currency, currency_from = (tracked.currency, "tracked_tickers") if tracked else (None, None)
    if currency is None:
        currency = _holdings_currency(conn, ticker, resolution)
        currency_from = "etf_holdings" if currency is not None else "nowhere"
    logger.info("ticker info: %s currency=%s from %s", ticker, currency, currency_from)

    return {
        "ticker": ticker,
        "exists": exists,
        "has_returns": first_date is not None,
        "first_date": first_date,
        "last_date": last_date,
        "status": tracked.status if tracked else None,
        "currency": currency,
        "fx_currency": major_currency(currency),
    }


def _holdings_currency(conn, ticker: str, resolution: TickerResolution) -> str | None:
    """The currency of the newest holding that ingest would price as `ticker`.

    Resolved forward, with ingest's own rules, rather than by stripping the suffix off
    `ticker`: overrides and per-exchange normalizers aren't reversible, and the same raw symbol
    on two exchanges is two companies. ~1.3k listings, so resolving them all is cheap."""
    for raw_ticker, exchange, currency in holdings_repository.get_listing_currencies(conn):
        symbol = holding_symbol(raw_ticker)
        if symbol is None:
            continue
        resolved = resolve_yfinance_ticker(
            symbol, exchange, resolution.exchange_suffixes, resolution.ticker_overrides
        )
        if resolved == ticker:
            return currency
    return None


def ticker_list(conn, source: str) -> dict:
    """Every ingested ticker for `source` -- what a client offers as the set of instruments it
    can price. Empty, not an error, when nothing has been ingested for `source`."""
    return {"tickers": returns_repository.list_tickers(conn, source)}


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
        rows = _usable_rates(
            base, quote, fx_repository.get_rate_series(conn, base, quote, source, start, end)
        )
        return _fx_result(base, quote, source, "direct", None, rows)

    if quote == config_base:
        rows = _usable_rates(
            quote, base, fx_repository.get_rate_series(conn, quote, base, source, start, end)
        )
        inverted = [(d, _quantize(Decimal(1) / rate)) for d, rate in rows]
        return _fx_result(base, quote, source, "inverse", None, inverted)

    numerator = dict(
        _usable_rates(
            config_base,
            quote,
            fx_repository.get_rate_series(conn, config_base, quote, source, start, end),
        )
    )
    denominator = dict(
        _usable_rates(
            config_base,
            base,
            fx_repository.get_rate_series(conn, config_base, base, source, start, end),
        )
    )
    shared_dates = sorted(set(numerator) & set(denominator))
    triangulated = [(d, _quantize(numerator[d] / denominator[d])) for d in shared_dates]
    return _fx_result(base, quote, source, "triangulated", config_base, triangulated)


def _usable_rates(stored_base: str, stored_quote: str, rows) -> list:
    """Drop dates whose stored rate cannot be a real quote, warning about each.

    `save_fx_rates` only rejects rates too *large* for the column, so a `0` (or a negative) can
    reach the table. Every such rate is dropped here, not just the ones this request would
    divide by: a rate of 0 is corrupt whichever derivation asks for it, and returning it
    verbatim on the direct path while 500-ing the inverse one for the same day would just move
    the division by zero into the caller. Dropping the date matches how the write side already
    handles a rate its column can't hold -- skip the row, warn, keep the rest of the range."""
    usable = []
    for rate_date, rate in rows:
        if rate is None or rate <= 0:
            logger.warning(
                "fx_rate_series: dropping %s/%s %s -- stored rate=%s is not a usable quote.",
                stored_base,
                stored_quote,
                rate_date,
                rate,
            )
            continue
        usable.append((rate_date, rate))
    return usable


def _quantize(rate: Decimal) -> Decimal:
    return rate.quantize(_FX_RATE_SCALE, rounding=ROUND_HALF_UP)


def _fx_result(base, quote, source, derivation, via, rows) -> dict:
    return {
        "base_currency": base,
        "quote_currency": quote,
        "source": source,
        "derivation": derivation,
        "via": via,
        "points": [{"date": d, "rate": rate} for d, rate in rows],
    }


def risk_free_series(conn, source: str, start: date, end: date) -> dict:
    """rf_eur's published days in [start, end], spliced from €STR and EONIA (risk_free.splice),
    plus the first day the whole series covers, so a caller can apply R4 to a window that
    starts before it. No forward-fill: filling weekends and holidays is portfolio's job (§4.1).

    Both raw series are read over the whole range; the splice drops what's on the wrong side
    of the cutoff. That's at most ~7.7k rows, so clamping each read isn't worth a second copy
    of the cutoff here."""
    eonia = risk_free_repository.get_risk_free_rate_series(conn, source, "eonia", start, end)
    estr = risk_free_repository.get_risk_free_rate_series(conn, source, "estr", start, end)
    points = splice(eonia, estr)

    first = risk_free_repository.get_first_risk_free_dates(conn, source, ["eonia", "estr"])
    first_date = spliced_first_date(first.get("eonia"), first.get("estr"))

    if first_date is None:
        logger.warning(
            "risk-free: nothing stored for source=%s -- has ingest-risk-free-rates run?", source
        )
    elif not points:
        logger.info(
            "risk-free: no published days in range %s..%s (first_date=%s).",
            start,
            end,
            first_date,
        )
    else:
        counts = {
            series: sum(p["series"] == series for p in points) for series in (EONIA_ADJUSTED, ESTR)
        }
        logger.info(
            "risk-free: %d point(s) %s..%s (eonia_adjusted=%d, estr=%d), first_date=%s.",
            len(points),
            start,
            end,
            counts[EONIA_ADJUSTED],
            counts[ESTR],
            first_date,
        )

    return {"source": source, "first_date": first_date, "points": points}


_REGIME_FIELDS = (
    "regime_id",
    "dimension",
    "scope",
    "name",
    "start_date",
    "end_date",
    "known_from",
    "severity",
    "source",
    "notes",
)


def regimes_overlapping(conn, start: date, end: date) -> dict:
    """The regimes overlapping [start, end], with their own dates (unclipped: the caller knows
    its calendar), plus how many regimes are stored at all, so an empty table -- ingest-regimes
    never ran, or ran without config/regimes.csv -- reads differently from an empty range."""
    rows = regimes_repository.get_overlapping_regimes(conn, start, end)
    total = regimes_repository.count_regimes(conn)

    if total == 0:
        logger.warning(
            "regimes: none stored -- has ingest-regimes run with config/regimes.csv present?"
        )
    elif not rows:
        logger.info("regimes: none overlapping %s..%s (of %d stored).", start, end, total)
    else:
        logger.info("regimes: %d overlapping %s..%s (of %d stored).", len(rows), start, end, total)

    return {
        "total_regimes": total,
        "regimes": [dict(zip(_REGIME_FIELDS, row)) for row in rows],
    }


_CONSTITUENT_FIELDS = (
    "ticker",
    "name",
    "sector",
    "asset_class",
    "location",
    "exchange",
    "currency",
    "weight_percent",
)


def etf_holdings_funds(conn, funds: list[FundConfig]) -> dict:
    """Every configured fund, enabled or not, with its tradeable ticker and newest snapshot date
    (None when it has never been scraped). It is how a caller gets from a ticker it holds
    (IWDA.AS) to the fund_key whose holdings it wants. A disabled fund is listed too: its old
    snapshot is still served, and a caller can say why it has nothing newer."""
    latest = holdings_repository.get_latest_as_of_dates(conn)
    result = [
        {
            "fund_key": fund.key,
            "display_name": fund.display_name,
            "ticker": fund.ticker,
            "enabled": fund.enabled,
            "latest_as_of_date": latest.get(fund.key),
        }
        for fund in funds
    ]
    logger.info(
        "etf-holdings: %d configured fund(s), %d with a snapshot.",
        len(result),
        sum(f["latest_as_of_date"] is not None for f in result),
    )
    return {"funds": result}


def latest_etf_holdings(
    conn, funds: list[FundConfig], fund_key: str, resolution: TickerResolution
) -> dict:
    """Every line of `fund_key`'s newest snapshot, cash and derivatives included, with weights
    as stored (a snapshot can sum to a little over 100; normalising is the caller's decision).

    Each line also carries `yfinance_ticker`: what ingest prices it as, resolved with ingest's
    own rules, or None for a line ingest doesn't price (not equity, no symbol, or an unmapped
    exchange). Serving it here keeps the resolution rules in one service: a caller matching a
    stock it holds directly (0700.HK) to a constituent would otherwise need a copy of them.

    Raises FundNotConfigured for a key outside etfs.yaml, NoHoldingsSnapshot for a configured
    fund never scraped."""
    fund = next((f for f in funds if f.key == fund_key), None)
    if fund is None:
        logger.warning("etf-holdings: fund %r is not configured in etfs.yaml.", fund_key)
        raise FundNotConfigured(fund_key)

    snapshot = holdings_repository.get_latest_snapshot(conn, fund_key)
    if snapshot is None:
        logger.warning(
            "etf-holdings: no holdings snapshot stored for %s -- has ingest-holdings run?",
            fund_key,
        )
        raise NoHoldingsSnapshot(fund_key)

    as_of_date, rows = snapshot
    logger.info("etf-holdings: %s snapshot %s, %d line(s).", fund_key, as_of_date, len(rows))

    constituents = [_constituent(row, resolution) for row in rows]

    equity = [c for c in constituents if _is_priced_line(c)]
    resolved = sum(c["yfinance_ticker"] is not None for c in equity)
    weight_sum = sum(
        (c["weight_percent"] for c in constituents if c["weight_percent"] is not None),
        Decimal(0),
    )
    logger.info(
        "etf-holdings: %s: %d line(s), weights sum to %s%%; %d equity line(s): %d resolved, "
        "%d unresolvable.",
        fund_key,
        len(constituents),
        weight_sum,
        len(equity),
        resolved,
        len(equity) - resolved,
    )

    return {
        "fund_key": fund.key,
        "ticker": fund.ticker,
        "as_of_date": as_of_date,
        "constituents": constituents,
    }


def _constituent(row: tuple, resolution: TickerResolution) -> dict:
    constituent = dict(zip(_CONSTITUENT_FIELDS, row))
    constituent["ticker"] = holding_symbol(constituent["ticker"])
    constituent["yfinance_ticker"] = (
        resolve_yfinance_ticker(
            constituent["ticker"],
            constituent["exchange"],
            resolution.exchange_suffixes,
            resolution.ticker_overrides,
        )
        if _is_priced_line(constituent)
        else None
    )
    return constituent


def _is_priced_line(constituent: dict) -> bool:
    """The lines ingest prices: equity with a real symbol. The Python twin of
    holdings_repository.get_latest_tickers' `asset_class ILIKE 'equity'` filter, plus
    pipeline's holding_symbol check."""
    return (
        constituent["ticker"] is not None and (constituent["asset_class"] or "").lower() == "equity"
    )
