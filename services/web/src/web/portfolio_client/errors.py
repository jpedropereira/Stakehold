"""Errors raised by PortfolioClient implementations.

One shared parent so routes.py can catch every failure with a single `except
PortfolioClientError`, the same role auth_client.errors.AuthClientError plays. Simpler than that
hierarchy, though: portfolio's own errors.py gives every domain exception a distinct HTTP status
code (see portfolio/docs/architecture.md's error-mapping table), so there's no auth-style "two
different causes collapse onto the same status" case to disambiguate by message text.
"""


class PortfolioClientError(Exception):
    """Base class for every failure calling the portfolio service."""


class AccountNotFound(PortfolioClientError):
    """portfolio returned 404 -- account_label didn't resolve for this user."""


class AccountLabelConflict(PortfolioClientError):
    """portfolio returned 409 -- the label is already used under a different broker."""


class TickerMappingNotFound(PortfolioClientError):
    """portfolio returned 404 on a ticker-mapping delete -- the mapping is already gone. Its own
    class because on every other route a 404 means AccountNotFound."""


class TransactionNotFound(PortfolioClientError):
    """portfolio returned 404 on a transaction delete. portfolio 404s an account that doesn't
    resolve for the user and a reference that isn't in the account alike, and either way there
    is nothing of the user's to delete, so the two aren't told apart here."""


class InvalidPortfolioRequest(PortfolioClientError):
    """portfolio returned 422 -- a caller-supplied value failed validation or parsing (a bad
    fx_rate, an unmapped broker, a malformed export file, and so on).

    Carries the server's own message verbatim, unlike auth_client's canned per-type copy:
    portfolio's validation messages are meant to be shown to the user as-is -- there's no
    enumeration/security concern here, just a caller mistake the user can fix.
    """


class PortfolioUnavailable(PortfolioClientError):
    """portfolio could not be reached, timed out, rejected the internal secret, or returned an
    unexpected/malformed response."""


# --- tracked tickers (ticker onboarding) ---------------------------------------------------
# Each is a different sentence for the user, so each gets its own class; on these routes a status
# code means something other than it does on the account routes (a 404 isn't AccountNotFound).


class InvalidTickerRequest(PortfolioClientError):
    """422: not a ticker symbol, or an index or currency pair, which can't be held."""


class TickerNotOnProvider(PortfolioClientError):
    """404 on add: the price provider doesn't know the symbol (often a delisted security)."""


class TickerLimitReached(PortfolioClientError):
    """429: the user has added as many new tickers as they may in a day."""


class TickerProviderUnavailable(PortfolioClientError):
    """503: the price provider didn't answer. Says nothing about the symbol; try again later."""


class TrackedTickerNotFound(PortfolioClientError):
    """404 on an admin change: not a tracked ticker."""


class TickerChangeRefused(PortfolioClientError):
    """409: an admin change data-store won't make (a YAML-managed ticker's enabled flag, or a
    retry of a ticker that hasn't failed)."""
