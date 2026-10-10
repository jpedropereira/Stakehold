"""Flows that bring a ticker into tracked_tickers and through onboarding.

A sibling of `pipeline.py` and `read_flows.py`, not an addition to `pipeline.py`, which is
already close to the context-management file-size guidance. Like both, it never touches SQL
directly: the table goes through `tracked_tickers/repository.py`, imported as a module so a test
can replace the whole repository namespace. The onboarding run reuses `pipeline`'s backfill and
returns flows, called through the module for the same reason.

It holds the YAML sync, the symbol lookup a user's request goes through, and (tasks.md's
TK-5) the onboarding run itself.
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from data_store import pipeline
from data_store.config_loader import CurrenciesConfig, CurrencyConfig, TickerConfig
from data_store.fx import repository as fx_repository
from data_store.returns import repository as returns_repository
from data_store.run_result import STATUS_PARTIAL, build_run_result
from data_store.tracked_tickers import repository as tracked_tickers_repository
from data_store.tracked_tickers.base import (
    OUTCOME_FOUND,
    OUTCOME_NOT_FOUND,
    OUTCOME_UNAVAILABLE,
    OUTCOME_UNSUPPORTED,
    LookupResult,
    TickerLookupSource,
)
from data_store.tracked_tickers.repository import TrackedTicker
from data_store.tracked_tickers.yfinance_source import YFinanceTickerLookupSource

logger = logging.getLogger(__name__)

# What a provider symbol can contain: letters, digits, and the punctuation yfinance uses for
# exchange suffixes (`.AS`), share classes and crypto pairs (`-`), futures and FX (`=`), and
# indexes (`^`). Whitelisted rather than blacklisted, and capped at the column width, so nothing
# else reaches the provider or the database.
_SYMBOL_PATTERN = re.compile(
    r"^[A-Z0-9.\-=^]{1,%d}$" % tracked_tickers_repository.TICKER_MAX_LENGTH
)

# How long a definite lookup answer is reused, so a user retyping a symbol doesn't spend the
# provider's rate limit.
LOOKUP_CACHE_TTL_SECONDS = 15 * 60


def normalize_symbol(raw: str) -> str | None:
    """The symbol in the form the provider and the table use, or None if it can't be one.
    Symbols are case-insensitive at the provider (TK-0), so uppercasing loses nothing."""
    symbol = raw.strip().upper()
    return symbol if _SYMBOL_PATTERN.fullmatch(symbol) else None


class LookupCache:
    """An in-process cache of lookup answers. Per process on purpose: the read API runs one
    uvicorn worker, and an answer that's briefly duplicated across workers would only cost one
    extra provider call each."""

    def __init__(
        self,
        *,
        ttl_seconds: float = LOOKUP_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[tuple[str, str], tuple[float, LookupResult]] = {}

    def get(self, source: str, symbol: str) -> LookupResult | None:
        entry = self._entries.get((source, symbol))
        if entry is None:
            return None
        stored_at, result = entry
        if self._clock() - stored_at > self._ttl_seconds:
            del self._entries[(source, symbol)]
            return None
        return result

    def put(self, source: str, symbol: str, result: LookupResult) -> None:
        self._entries[(source, symbol)] = (self._clock(), result)


def lookup_symbol(symbol: str, source: TickerLookupSource, cache: LookupCache) -> LookupResult:
    """Ask `source` about an already-normalized symbol, reusing a recent definite answer.

    "Unavailable" is never cached: it says the provider didn't answer, not anything about the
    symbol, so the next attempt must ask again.
    """
    cached = cache.get(source.name, symbol)
    if cached is not None:
        return cached
    result = source.lookup(symbol)
    if result.outcome != OUTCOME_UNAVAILABLE:
        cache.put(source.name, symbol, result)
    return result


def sync_config_tickers(conn, config_tickers: list[TickerConfig]) -> dict[str, int | str]:
    """Bring tracked_tickers in line with every entry in tracked_tickers.yaml, disabled ones
    included.

    The YAML is authoritative for every ticker it names. A ticker the table doesn't have is
    inserted as a `pending` config row, for onboarding to backfill. A ticker it has gets the
    YAML's display name, asset type and enabled flag, and becomes a config row even if a user
    requested it first. Onboarding state (status, attempts, requested_by) is never touched.

    A config row whose ticker has gone from the YAML is left as it is, with a warning. Deleting
    it would orphan its price history from anything that follows it, and disabling it would
    guess at intent; `enabled: false` in the YAML is the explicit way to stop pricing a ticker.
    """
    logger.info("sync-tracked-tickers: %d ticker(s) in tracked_tickers.yaml.", len(config_tickers))
    if not config_tickers:
        logger.info("sync-tracked-tickers: no tickers in tracked_tickers.yaml, nothing to do.")

    existing = {
        (row.ticker, row.source): row
        for row in tracked_tickers_repository.get_tracked_tickers(conn)
    }

    inserts: list[TickerConfig] = []
    updates: list[TickerConfig] = []
    skipped = 0
    for entry in config_tickers:
        if len(entry.ticker) > tracked_tickers_repository.TICKER_MAX_LENGTH:
            logger.warning(
                "sync-tracked-tickers: skipping %r -- longer than %d characters, which no "
                "provider ticker is. Fix it in tracked_tickers.yaml.",
                entry.ticker,
                tracked_tickers_repository.TICKER_MAX_LENGTH,
            )
            skipped += 1
            continue

        row = existing.get((entry.ticker, entry.price_source))
        if row is None:
            inserts.append(entry)
        elif (
            row.origin != tracked_tickers_repository.ORIGIN_CONFIG
            or row.display_name != entry.display_name
            or row.asset_type != entry.asset_type
            or row.enabled != entry.enabled
        ):
            updates.append(entry)

    named = {(entry.ticker, entry.price_source) for entry in config_tickers}
    orphaned = sorted(
        key
        for key, row in existing.items()
        if row.origin == tracked_tickers_repository.ORIGIN_CONFIG and key not in named
    )
    for ticker, source in orphaned:
        logger.warning(
            "sync-tracked-tickers: %s (%s) is a config row but no longer in "
            "tracked_tickers.yaml -- left as it is. Set `enabled: false` in the YAML to stop "
            "pricing it.",
            ticker,
            source,
        )

    if inserts or updates:
        added, updated = tracked_tickers_repository.save_config_tickers(
            conn, inserts=inserts, updates=updates
        )
    else:
        added = updated = 0
        if config_tickers:
            logger.info("sync-tracked-tickers: tracked_tickers is already up to date.")

    logger.info(
        "sync-tracked-tickers: done, %d added, %d updated, %d skipped, %d orphaned.",
        added,
        updated,
        skipped,
        len(orphaned),
    )
    return build_run_result(
        units_total=len(config_tickers),
        rows_saved=added + updated,
        failed=skipped,
        tickers_added=added,
        tickers_updated=updated,
        tickers_skipped=skipped,
        tickers_orphaned=len(orphaned),
    )


def tracked_ticker_configs(conn, *, ready_only: bool) -> list[TickerConfig]:
    """The tracked tickers a pricing flow should follow, in the shape the flows already take.

    Disabled rows are never included. `ready_only` is for the daily ingest: a ticker that isn't
    `ready` yet must not get a rolling window of rows ahead of its backfill, because backfill's
    resume rule skips any ticker that already has data, and would then never fetch its history.
    Backfill itself passes `ready_only=False`, since filling the history is what makes a ticker
    ready.
    """
    return [
        TickerConfig(
            ticker=row.ticker,
            display_name=row.display_name,
            asset_type=row.asset_type,
            price_source=row.source,
            enabled=True,
        )
        for row in tracked_tickers_repository.get_tracked_tickers(conn)
        if row.enabled and (not ready_only or row.status == tracked_tickers_repository.STATUS_READY)
    ]


# Currencies yfinance quotes some listings in, as minor units of a major currency. FX rates exist
# for the major one only. Verified for GBp (TK-0); ZAc and ILA are yfinance's documented
# equivalents for Johannesburg and Tel Aviv.
_MINOR_TO_MAJOR_CURRENCY = {"GBp": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


def major_currency(code: str | None) -> str | None:
    """The ISO code FX rates are tracked under for a provider currency, or None if it isn't one.

    This only picks which FX pair a ticker needs. A price quoted in pence is still stored in
    pence; nothing rescales it (a known limitation, see tasks.md).
    """
    if code is None:
        return None
    code = _MINOR_TO_MAJOR_CURRENCY.get(code, code)
    return code if _CURRENCY_CODE.fullmatch(code) else None


def currencies_with_tracked_tickers(conn, currencies: CurrenciesConfig) -> CurrenciesConfig:
    """currencies.yaml, widened with the currency of every enabled tracked ticker.

    A ticker nobody can convert into the base currency can't be valued, so its currency joins
    the FX universe automatically rather than waiting for someone to edit the YAML. A currency
    the YAML explicitly disables stays disabled -- that's the operator's decision -- with a
    warning naming the tickers it leaves unconvertible.
    """
    needed: dict[str, list[str]] = {}
    for row in tracked_tickers_repository.get_tracked_tickers(conn):
        code = major_currency(row.currency)
        if row.enabled and code is not None:
            needed.setdefault(code, []).append(row.ticker)

    configured = {c.code: c for c in currencies.currencies}
    for code, tickers in sorted(needed.items()):
        if code in configured and not configured[code].enabled:
            logger.warning(
                "FX universe: %s is disabled in currencies.yaml, so %s can't be converted to %s. "
                "Enable it there if that's not intended.",
                code,
                ", ".join(sorted(tickers)),
                currencies.base_currency,
            )

    added = sorted(code for code in needed if code not in configured)
    if not added:
        return currencies
    logger.info(
        "FX universe: adding %s to the FX universe for tracked tickers' currencies.",
        ", ".join(added),
    )
    return CurrenciesConfig(
        base_currency=currencies.base_currency,
        currencies=[
            *currencies.currencies,
            *(CurrencyConfig(code=code, enabled=True) for code in added),
        ],
    )


# Onboarding attempts before a ticker is given up on and marked `failed`.
MAX_ATTEMPTS = 3
# Wait before the first retry; it doubles for each one after (10, then 20 minutes).
RETRY_BASE_DELAY = timedelta(minutes=10)
# A `backfilling` row untouched for this long belongs to a run that died. One ticker's onboarding
# takes seconds to a minute, so an hour leaves a wide margin.
STUCK_AFTER = timedelta(hours=1)
# Bounds one run's length. The DAG runs every few minutes, so anything past the cap is picked up
# by the next run rather than lost.
MAX_TICKERS_PER_RUN = 50
# Sleep after each provider fetch, the backfill flows' own rate-limit courtesy. Smaller than a
# full backfill's 10 s, since an onboarding run fetches a handful of tickers, not thousands.
FETCH_DELAY_SECONDS = 2.0


class OnboardingError(Exception):
    """An attempt failed for a reason worth showing whoever requested the ticker."""


def process_ticker_requests(
    conn,
    *,
    currencies: CurrenciesConfig,
    lookup_source: TickerLookupSource,
    now: datetime,
    today: date,
) -> dict[str, int | str]:
    """One onboarding run: take each due `pending` ticker to `ready`, or record why it couldn't.

    A ticker is `ready` only when it has stored prices, FX rates for its currency (unless that
    is the base currency, or the YAML disables it), and calculated returns -- everything a
    portfolio needs to value it. Success is judged by what landed in the tables, not by the
    flows' statuses alone: `backfill_market_data` absorbs a per-ticker fetch failure and reports
    it as "no data", so a ticker with no stored prices afterwards is a failure.

    A failed attempt is retried with a doubling delay, up to MAX_ATTEMPTS. Only a ticker given up
    on makes the run `partial` (and the DAG red); a retry still to come is logged and counted,
    so a passing provider blip doesn't page anyone.
    """
    reset = tracked_tickers_repository.reset_stuck_backfilling(conn, stale_before=now - STUCK_AFTER)
    for ticker in reset:
        logger.warning(
            "process-ticker-requests: %s was left backfilling by a run that didn't finish -- "
            "back to pending.",
            ticker,
        )

    due = tracked_tickers_repository.get_due_tickers(conn, now=now, limit=MAX_TICKERS_PER_RUN)
    logger.info("process-ticker-requests: %d ticker(s) due for onboarding.", len(due))
    if not due:
        logger.info("process-ticker-requests: no tickers waiting, nothing to do.")

    rows_saved = ready = retrying = gave_up = 0
    for index, row in enumerate(due, start=1):
        if not tracked_tickers_repository.claim_for_backfill(conn, row.ticker, row.source):
            logger.info("process-ticker-requests: %s is no longer pending, leaving it.", row.ticker)
            continue
        logger.info("process-ticker-requests: [%d/%d] onboarding %s.", index, len(due), row.ticker)
        try:
            saved = _onboard(conn, row, currencies, lookup_source, today)
        except OnboardingError as e:
            error = str(e)
        except Exception as e:
            logger.warning(
                "process-ticker-requests: %s failed unexpectedly: %s", row.ticker, e, exc_info=True
            )
            error = f"unexpected error: {e}"
        else:
            tracked_tickers_repository.mark_ready(conn, row.ticker, row.source)
            rows_saved += saved
            ready += 1
            logger.info("process-ticker-requests: %s is ready.", row.ticker)
            continue

        attempts = row.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            status = tracked_tickers_repository.STATUS_FAILED
            next_attempt_at = None
            gave_up += 1
            logger.warning(
                "process-ticker-requests: %s failed (attempt %d of %d), giving up: %s",
                row.ticker,
                attempts,
                MAX_ATTEMPTS,
                error,
            )
        else:
            status = tracked_tickers_repository.STATUS_PENDING
            next_attempt_at = now + RETRY_BASE_DELAY * 2 ** (attempts - 1)
            retrying += 1
            logger.warning(
                "process-ticker-requests: %s failed (attempt %d of %d), retrying after %s: %s",
                row.ticker,
                attempts,
                MAX_ATTEMPTS,
                next_attempt_at.isoformat(),
                error,
            )
        tracked_tickers_repository.record_failed_attempt(
            conn,
            row.ticker,
            row.source,
            attempts=attempts,
            status=status,
            next_attempt_at=next_attempt_at,
            error=error,
        )

    logger.info(
        "process-ticker-requests: done, %d ready, %d retrying, %d failed, %d reset.",
        ready,
        retrying,
        gave_up,
        len(reset),
    )
    return build_run_result(
        units_total=len(due),
        rows_saved=rows_saved,
        failed=gave_up,
        tickers_due=len(due),
        tickers_ready=ready,
        tickers_retrying=retrying,
        tickers_failed=gave_up,
        tickers_reset=len(reset),
    )


def _onboard(conn, row, currencies: CurrenciesConfig, lookup_source, today: date) -> int:
    """Backfill one ticker's full history, its currency's FX rates, and its returns. Returns the
    price and FX rows saved; raises OnboardingError with a readable reason on failure."""
    currency = row.currency
    if currency is None:
        currency = _fill_listing_details(conn, row, lookup_source)

    config = TickerConfig(
        ticker=row.ticker,
        display_name=row.display_name,
        asset_type=row.asset_type,
        price_source=row.source,
        enabled=True,
    )
    # No earliest date: backfill's own "as far back as the source goes" for a ticker with no
    # rows, and "already covered" for one that has them (a YAML ticker priced before onboarding
    # existed). No funds: only this ticker.
    backfill = pipeline.backfill_market_data(
        conn,
        [],
        [config],
        {},
        None,
        today,
        FETCH_DELAY_SECONDS,
        only_tickers={row.ticker},
    )
    if backfill["status"] == STATUS_PARTIAL:
        raise OnboardingError("the price history couldn't be saved")
    if not returns_repository.ticker_exists(conn, row.ticker, row.source):
        raise OnboardingError("the provider returned no price history")
    rows_saved = backfill["rows_saved"]

    rows_saved += _backfill_currency(conn, row.ticker, currency, currencies, today)

    returns = pipeline.calculate_returns(conn, only_tickers={row.ticker})
    if returns["status"] == STATUS_PARTIAL:
        raise OnboardingError("returns couldn't be calculated")
    return rows_saved


def _fill_listing_details(conn, row, lookup_source) -> str | None:
    """A config row arrives without a currency; ask the provider for it. Not being able to is
    logged, not fatal: the prices can still load, and the next run asks again."""
    result = lookup_source.lookup(row.ticker)
    if result.outcome not in (OUTCOME_FOUND, OUTCOME_UNSUPPORTED):
        logger.warning(
            "process-ticker-requests: couldn't read the currency of %s (%s), so no FX rates "
            "will be loaded for it this time.",
            row.ticker,
            result.outcome,
        )
        return None
    tracked_tickers_repository.set_listing_details(
        conn, row.ticker, row.source, currency=result.currency, exchange=result.exchange
    )
    return result.currency


def _backfill_currency(
    conn, ticker: str, currency: str | None, currencies: CurrenciesConfig, today: date
) -> int:
    code = major_currency(currency)
    if code is None or code == currencies.base_currency:
        return 0
    widened = currencies_with_tracked_tickers(conn, currencies)
    if not any(c.code == code and c.enabled for c in widened.currencies):
        # Disabled in currencies.yaml; currencies_with_tracked_tickers has already warned.
        return 0
    fx = pipeline.backfill_fx_rates(
        conn, widened, None, today, FETCH_DELAY_SECONDS, only_currencies={code}
    )
    pair = (currencies.base_currency, code)
    if fx["status"] == STATUS_PARTIAL or not fx_repository.get_earliest_fx_dates(
        conn, "yfinance", [pair]
    ):
        raise OnboardingError(f"FX rates for {code} couldn't be loaded")
    return fx["rows_saved"]


# --- requests: look a symbol up, add it, change it ------------------------------------------------

# Lookup providers by the `source` name stamped into every table. One today; a second provider is
# a new TickerLookupSource and one line here.
TICKER_LOOKUP_SOURCES = {"yfinance": YFinanceTickerLookupSource}

# One per process, shared by every request the API serves. See LookupCache for why per process.
LOOKUP_CACHE = LookupCache()

# The window the per-user request limit counts over.
REQUEST_LIMIT_WINDOW = timedelta(days=1)

# What lookup_ticker reports, beyond the provider's own outcomes: "tracked" means this service
# already follows the ticker (its `status` says how far onboarding has got), and "available"
# means the provider has it and it can be added.
LOOKUP_TRACKED = "tracked"
LOOKUP_AVAILABLE = "available"
_LOOKUP_OUTCOMES = {
    OUTCOME_FOUND: LOOKUP_AVAILABLE,
    OUTCOME_UNSUPPORTED: OUTCOME_UNSUPPORTED,
    OUTCOME_NOT_FOUND: OUTCOME_NOT_FOUND,
    OUTCOME_UNAVAILABLE: OUTCOME_UNAVAILABLE,
}


class InvalidSymbol(Exception):
    """Not something a provider symbol can be -- refused before anything is asked."""


class UnknownLookupSource(Exception):
    """No lookup provider is registered under this source name."""


class NotOnProvider(Exception):
    """The provider doesn't know the symbol, or has no prices for it."""


class CannotBeHeld(Exception):
    """The provider has the symbol, but it's an index or a currency pair."""


class ProviderUnavailable(Exception):
    """The provider didn't answer. Says nothing about the symbol."""


class RequestLimitReached(Exception):
    def __init__(self, limit: int):
        super().__init__(f"limit of {limit} new ticker(s) per day reached")
        self.limit = limit


class TrackedTickerNotFound(Exception):
    pass


class ManagedByConfig(Exception):
    """Enabling or disabling a config row through the API: the next YAML sync would undo it."""


class NotRetryable(Exception):
    """Only a `failed` ticker can be put back in the onboarding queue."""


def lookup_source_for(source: str) -> TickerLookupSource:
    try:
        return TICKER_LOOKUP_SOURCES[source]()
    except KeyError:
        raise UnknownLookupSource(source) from None


def _require_symbol(raw: str) -> str:
    symbol = normalize_symbol(raw)
    if symbol is None:
        raise InvalidSymbol(raw)
    return symbol


def lookup_ticker(conn, raw: str, lookup_source: TickerLookupSource, cache: LookupCache) -> dict:
    """What a user would get by adding `raw`: the ticker this service already follows (and how
    far its onboarding has got), or the provider's answer about it.

    A tracked ticker is answered from the table without asking the provider. The provider can
    spell a symbol differently from how it was typed, so its canonical spelling is checked
    against the table too.
    """
    symbol = _require_symbol(raw)
    row = tracked_tickers_repository.get_tracked_ticker(conn, symbol, lookup_source.name)
    if row is not None:
        return _tracked_lookup(row)

    result = lookup_symbol(symbol, lookup_source, cache)
    if result.ticker != symbol:
        row = tracked_tickers_repository.get_tracked_ticker(conn, result.ticker, lookup_source.name)
        if row is not None:
            return _tracked_lookup(row)
    return {
        "ticker": result.ticker,
        "outcome": _LOOKUP_OUTCOMES[result.outcome],
        "status": None,
        "display_name": result.display_name,
        "currency": result.currency,
        "exchange": result.exchange,
        "asset_type": result.asset_type,
    }


def _tracked_lookup(row: TrackedTicker) -> dict:
    return {
        "ticker": row.ticker,
        "outcome": LOOKUP_TRACKED,
        "status": row.status,
        "display_name": row.display_name,
        "currency": row.currency,
        "exchange": row.exchange,
        "asset_type": row.asset_type,
    }


def add_tracked_ticker(
    conn,
    raw: str,
    *,
    requested_by,
    enforce_limit: bool,
    limit_per_day: int,
    lookup_source: TickerLookupSource,
    cache: LookupCache,
    now: datetime,
) -> tuple[TrackedTicker, bool]:
    """Add a user's ticker as a `pending` request row for onboarding to pick up. Returns the
    row and whether this call created it.

    A ticker already tracked is returned as it is: re-adding it is free, so it neither counts
    toward the limit nor asks the provider. Otherwise the limit is checked first (cheapest, and
    spares the provider), then the provider, which is asked again here rather than trusting an
    earlier lookup by the caller.

    The limit counts rows a user created in the last 24 hours. Two simultaneous requests from
    one user can both pass the count and land one over the limit; accepted, since the limit is
    there to stop volume, not to be exact.
    """
    symbol = _require_symbol(raw)
    row = tracked_tickers_repository.get_tracked_ticker(conn, symbol, lookup_source.name)
    if row is not None:
        return row, False

    if enforce_limit:
        recent = tracked_tickers_repository.count_requests_since(
            conn, requested_by, now - REQUEST_LIMIT_WINDOW
        )
        if recent >= limit_per_day:
            logger.info(
                "add-tracked-ticker: %s refused for %s -- %d request(s) in the last day, limit %d.",
                symbol,
                requested_by,
                recent,
                limit_per_day,
            )
            raise RequestLimitReached(limit_per_day)

    result = lookup_symbol(symbol, lookup_source, cache)
    if result.outcome == OUTCOME_NOT_FOUND:
        raise NotOnProvider(symbol)
    if result.outcome == OUTCOME_UNSUPPORTED:
        raise CannotBeHeld(result.display_name or symbol)
    if result.outcome == OUTCOME_UNAVAILABLE:
        raise ProviderUnavailable(symbol)

    row = tracked_tickers_repository.get_tracked_ticker(conn, result.ticker, lookup_source.name)
    if row is not None:
        return row, False

    created = tracked_tickers_repository.insert_requested_ticker(
        conn,
        ticker=result.ticker,
        source=lookup_source.name,
        display_name=result.display_name,
        asset_type=result.asset_type,
        currency=result.currency,
        exchange=result.exchange,
        requested_by=requested_by,
    )
    if created:
        logger.info(
            "add-tracked-ticker: %s requested by %s, pending onboarding.",
            result.ticker,
            requested_by,
        )
    return (
        tracked_tickers_repository.get_tracked_ticker(conn, result.ticker, lookup_source.name),
        created,
    )


def list_tracked(conn, *, source: str, requested_by=None, statuses=None, tickers=None):
    return tracked_tickers_repository.list_tracked_tickers(
        conn, source=source, requested_by=requested_by, statuses=statuses, tickers=tickers
    )


def update_tracked(
    conn, ticker: str, source: str, *, enabled: bool | None, retry: bool
) -> TrackedTicker:
    """An admin's change to one tracked ticker: enable or disable it, or put a `failed` one back
    in the onboarding queue."""
    row = tracked_tickers_repository.get_tracked_ticker(conn, ticker, source)
    if row is None:
        raise TrackedTickerNotFound(ticker)

    if enabled is not None and enabled != row.enabled:
        if row.origin == tracked_tickers_repository.ORIGIN_CONFIG:
            raise ManagedByConfig(ticker)
        tracked_tickers_repository.set_enabled(conn, ticker, source, enabled)
        logger.info("update-tracked-ticker: %s %s.", ticker, "enabled" if enabled else "disabled")

    if retry:
        if row.status != tracked_tickers_repository.STATUS_FAILED:
            raise NotRetryable(row.status)
        tracked_tickers_repository.reset_for_retry(conn, ticker, source)
        logger.info("update-tracked-ticker: %s back in the onboarding queue.", ticker)

    return tracked_tickers_repository.get_tracked_ticker(conn, ticker, source)
