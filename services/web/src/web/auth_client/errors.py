"""Errors raised by AuthClient implementations.

One shared parent so routes.py can catch every failure with a single `except AuthClientError`,
the same way auth's own service.py exceptions are all mapped to HTTP by one set of handlers.
"""


class AuthClientError(Exception):
    """Base class for every failure calling the auth service."""


class InvalidCredentials(AuthClientError):
    """auth returned 401 -- wrong password or unknown email (deliberately indistinguishable)."""


class EmailAlreadyRegistered(AuthClientError):
    """auth returned 409 on signup -- the email is already registered."""


class RateLimited(AuthClientError):
    """auth returned 429 -- the login or signup throttle tripped."""


class InvalidRequest(AuthClientError):
    """auth returned 422 -- the submitted email/password/display_name failed its validation
    rules (e.g. a password under 12 characters). Kept distinct from AuthUnavailable so the user
    sees "check your details" rather than "the service is down"."""


class SessionInvalid(AuthClientError):
    """auth returned 401 on session validation -- missing, unknown, or expired session."""


class AuthUnavailable(AuthClientError):
    """auth could not be reached, timed out, or returned an unexpected server error."""
