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
