import secrets

from fastapi import Request, Response

# The __Host- prefix requires the Secure attribute, an unset Domain, and Path=/ -- so the name
# has to follow cookie_secure rather than being fixed, or every local http (non-TLS) request
# would silently fail CSRF because the browser refuses to store a __Host- cookie without TLS.
_SECURE_COOKIE_NAME = "__Host-stakehold_csrf"
_INSECURE_COOKIE_NAME = "stakehold_csrf"

_TOKEN_BYTES = 32


class CsrfInvalid(Exception):
    """The submitted form token didn't match the browser's CSRF cookie, or either was missing."""


def cookie_name(*, cookie_secure: bool) -> str:
    return _SECURE_COOKIE_NAME if cookie_secure else _INSECURE_COOKIE_NAME


def ensure_csrf_cookie(request: Request, response: Response, *, cookie_secure: bool) -> str:
    """Returns the CSRF token for this browser, issuing a fresh one if none exists yet.

    Called on every GET that renders a form, so the very first page load already has a token to
    embed as a hidden field -- there's no separate "fetch a token" round trip.
    """
    name = cookie_name(cookie_secure=cookie_secure)
    existing = request.cookies.get(name)
    if existing:
        return existing

    token = secrets.token_urlsafe(_TOKEN_BYTES)
    response.set_cookie(
        key=name,
        value=token,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        path="/",
    )
    return token


def verify_csrf(request: Request, *, form_token: str | None, cookie_secure: bool) -> None:
    """Raises CsrfInvalid unless the cookie and the submitted hidden field agree.

    Stateless double-submit: no server-side session store is needed because a cross-site
    forged POST can neither read the victim's cookie value nor have it sent along
    (SameSite=Lax cookies aren't attached to cross-site POSTs), so it can never supply a
    matching pair.
    """
    cookie_token = request.cookies.get(cookie_name(cookie_secure=cookie_secure))
    if cookie_token is None:
        # Falls back to the other name so a rolling deploy that briefly serves a request with a
        # different WEB_COOKIE_SECURE than the one that issued the browser's cookie (mixed
        # replica config, or a mid-rollout flip) doesn't hard-fail CSRF for every visitor until
        # their browser reloads and is reissued a token under the new setting. Safe to accept
        # either name: which cookie matches the submitted form token is what proves the request
        # came from this browser, not which name it happened to be stored under.
        cookie_token = request.cookies.get(cookie_name(cookie_secure=not cookie_secure))
    if not cookie_token or not form_token:
        raise CsrfInvalid("missing CSRF cookie or form token")

    # Compared as bytes, not str: secrets.compare_digest raises TypeError for any str character
    # above ASCII, so a forged high-byte token would turn a rejected request into a 500 instead
    # of a clean CsrfInvalid.
    if not secrets.compare_digest(cookie_token.encode("utf-8"), form_token.encode("utf-8")):
        raise CsrfInvalid("CSRF token mismatch")
