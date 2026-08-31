"""Domain-level errors raised by pipeline.py and the pure validators it calls.

One shared parent so jobs.py can catch every domain failure with a single `except PortfolioError`
and turn it into a `partial` run_result, the same way auth's service.py exceptions are all mapped
to HTTP by one set of handlers. Repositories never raise these -- they let the driver's own
exceptions (e.g. psycopg.errors.UniqueViolation) propagate, same as auth.
"""


class PortfolioError(Exception):
    """Base class for every domain-level failure in this service."""


class UnknownBroker(PortfolioError):
    """`account add` named a broker outside the known registry."""


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
