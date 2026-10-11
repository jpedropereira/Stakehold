"""Domain-level errors raised by pipeline.py and the pure validators it calls.

One shared parent so cli.py and the HTTP API can each handle every domain failure through a
single `except PortfolioError`/exception handler, the same way auth's service.py exceptions are
all mapped to HTTP by one set of handlers -- jobs.py itself no longer catches PortfolioError; see
its own module docstring. Repositories never raise these -- they let the driver's own exceptions
(e.g. psycopg.errors.UniqueViolation) propagate, same as auth.

Every subclass sets its own `status_code`, read by api/app.py's single exception handler -- it
lives here, next to the error it classifies, rather than in a separate table in app.py, so a new
subclass can't be added without a status code sitting right next to it to fill in. Unset, it's
500 ("fails closed" -- api/app.py's own docs), matching cli.py's generic one-line-and-exit-1
handling for anything it doesn't special-case.
"""


class PortfolioError(Exception):
    """Base class for every domain-level failure in this service."""

    status_code: int = 500


class BrokerParserNotFound(PortfolioError):
    """`import-transactions`/`import-cashflow` resolved `--account` to an account whose own
    `broker` column has no parser currently registered under BROKER_PARSERS/CASHFLOW_PARSERS --
    either a typo when the account was created, or the plugin package providing that broker's
    parser isn't installed. Never raised by `account add`: a broker is a free-text label there,
    and account creation must work with zero broker plugins installed (see InvalidBroker below
    for the one check it does perform)."""

    status_code = 422


class BrokerPluginError(PortfolioError):
    """A broker-parser or cashflow-parser plugin's entry-point registration is malformed -- two
    installed plugins claim the same key within one registry group, or a loaded parser's own
    `broker_key` disagrees with the name it registered under. A startup/config-time failure
    (raised by broker_import/discovery.py), not a per-request one -- an installed plugin set is
    either internally consistent or the whole registry build fails loudly, never silently picks
    one of two conflicting registrations."""

    status_code = 500


class InvalidBroker(PortfolioError):
    """`account add --broker` was blank/whitespace-only, or longer than accounts.broker's column
    can hold. The only validation account creation performs on `broker` -- it's a free-text
    label, not checked against a fixed set of names."""

    status_code = 422


class AccountNotFound(PortfolioError):
    """`--account <label>` did not resolve to an account owned by the given user.

    Deliberately the same error whether the label doesn't exist at all or belongs to a
    different user -- accounts/repository.py's get_by_label doesn't distinguish the two, which
    is what makes the isolation chokepoint structural rather than a convention.
    """

    status_code = 404


class AccountLabelConflict(PortfolioError):
    """`account add` re-used a label already taken by this user, under a different broker.

    Re-running with the same broker is a safe no-op (nothing_to_do); disagreeing about the
    broker is not, and must not be swallowed silently.
    """

    status_code = 409


class TransactionNotFound(PortfolioError):
    """No transaction with that reference in the given account -- already deleted, most likely.
    Only raised once the account itself resolved for the caller; an account that doesn't is
    AccountNotFound, so another user's transactions are never distinguishable from missing ones.
    """

    status_code = 404


class InvalidTransaction(PortfolioError):
    """A CanonicalTransaction failed transactions/base.py's validation rules."""

    status_code = 422


class BrokerFileError(PortfolioError):
    """A broker export file could not be parsed -- malformed header, unrecognized transaction
    type, or a row missing data its own type requires. Raised by broker_import/ parsers; never
    silently skipped, per docs/broker-import.md's "unrecognized type is a parse error" rule."""

    status_code = 422


class TickerMappingError(PortfolioError):
    """A stored instrument carries an identifier kind no mapping knows how to resolve -- bad data
    in the ledger, not a caller mistake."""

    status_code = 500


class InvalidTickerMapping(PortfolioError):
    """A ticker mapping an admin tried to save breaks a rule: a kind other than ISIN/SYMBOL, an
    ISIN with a broker, a SYMBOL without one, a blank or over-long value. Shown to the admin
    as-is."""

    status_code = 422


class TickerMappingNotFound(PortfolioError):
    """No ticker mapping has that id -- already deleted, most likely."""

    status_code = 404


class UnknownTicker(PortfolioError):
    """A TICKER-kind instrument data-store has never ingested -- a typo, or an instrument
    Stakehold doesn't price. Its message is shown to the user as-is (web displays 422 detail
    verbatim), so it's written for them, not for a log."""

    status_code = 422


class DataStoreApiError(PortfolioError):
    """A call to data-store's read API (datastore_client) failed -- an error response, an
    unreachable host, or a malformed one."""

    status_code = 502


class TickerRequestRefused(PortfolioError):
    """data-store refused a tracked-ticker request for a reason the user has to be told: the
    symbol isn't on the provider (404), can't be held (422), is over the requester's daily limit
    (429), the provider didn't answer (503), or an admin change isn't allowed (409).

    Keeps data-store's own status code, so web can tell these apart from a status code alone,
    rather than all becoming the 502 a DataStoreApiError is. The class-level 502 is only the
    fallback for one raised without a code."""

    status_code = 502

    def __init__(self, detail: str, status_code: int | None = None):
        super().__init__(detail)
        if status_code is not None:
            self.status_code = status_code
