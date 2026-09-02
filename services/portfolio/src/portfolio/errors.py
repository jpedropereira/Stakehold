"""Domain-level errors raised by pipeline.py and the pure validators it calls.

One shared parent so jobs.py can catch every domain failure with a single `except PortfolioError`
and turn it into a `partial` run_result, the same way auth's service.py exceptions are all mapped
to HTTP by one set of handlers. Repositories never raise these -- they let the driver's own
exceptions (e.g. psycopg.errors.UniqueViolation) propagate, same as auth.
"""


class PortfolioError(Exception):
    """Base class for every domain-level failure in this service."""


class BrokerParserNotFound(PortfolioError):
    """`import-transactions`/`import-cashflow` resolved `--account` to an account whose own
    `broker` column has no parser currently registered under BROKER_PARSERS/CASHFLOW_PARSERS --
    either a typo when the account was created, or the plugin package providing that broker's
    parser isn't installed. Never raised by `account add`: a broker is a free-text label there,
    and account creation must work with zero broker plugins installed (see InvalidBroker below
    for the one check it does perform)."""


class BrokerPluginError(PortfolioError):
    """A broker-parser or cashflow-parser plugin's entry-point registration is malformed -- two
    installed plugins claim the same key within one registry group, or a loaded parser's own
    `broker_key` disagrees with the name it registered under. A startup/config-time failure
    (raised by broker_import/discovery.py), not a per-request one -- an installed plugin set is
    either internally consistent or the whole registry build fails loudly, never silently picks
    one of two conflicting registrations."""


class InvalidBroker(PortfolioError):
    """`account add --broker` was blank/whitespace-only, or longer than accounts.broker's column
    can hold. The only validation account creation performs on `broker` -- it's a free-text
    label, not checked against a fixed set of names."""


class AccountNotFound(PortfolioError):
    """`--account <label>` did not resolve to an account owned by the given user.

    Deliberately the same error whether the label doesn't exist at all or belongs to a
    different user -- accounts/repository.py's get_by_label doesn't distinguish the two, which
    is what makes the isolation chokepoint structural rather than a convention.
    """


class AccountLabelConflict(PortfolioError):
    """`account add` re-used a label already taken by this user, under a different broker.

    Re-running with the same broker is a safe no-op (nothing_to_do); disagreeing about the
    broker is not, and must not be swallowed silently.
    """


class InvalidTransaction(PortfolioError):
    """A CanonicalTransaction failed transactions/base.py's validation rules."""


class BrokerFileError(PortfolioError):
    """A broker export file could not be parsed -- malformed header, unrecognized transaction
    type, or a row missing data its own type requires. Raised by broker_import/ parsers; never
    silently skipped, per docs/architecture.md's "unrecognized type is a parse error" rule."""


class TickerMappingError(PortfolioError):
    """configs/ticker_mappings.yaml is missing, malformed, or fails validation."""


class DataStoreApiError(PortfolioError):
    """A call to data-store's read API (datastore_client) failed -- an error response, an
    unreachable host, or a malformed one. Kept in the single PortfolioError hierarchy so
    jobs.py's one `except PortfolioError` still catches it, same as every other domain error."""
