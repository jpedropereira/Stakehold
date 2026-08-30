from datetime import datetime

from fastapi import Response

# The one place the cookie's name and flags are decided -- see architecture.md's "Key decisions"
# for why these flags (HttpOnly/Secure/SameSite=Lax) and why auth, not web, sets this cookie.
COOKIE_NAME = "stakehold_session"


def set_session_cookie(
    response: Response, *, token: str, expires_at: datetime, now: datetime, secure: bool
) -> None:
    max_age = max(0, int((expires_at - now).total_seconds()))
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
