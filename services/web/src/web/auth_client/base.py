"""The AuthClient Protocol, matching portfolio's DataStoreClient split (base.py Protocol +
http_client.py implementation) -- so routes.py can be tested against a fake with no live auth
service, and http_client.py stays the only place that knows HTTP status codes and cookie names.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: str
    display_name: str


@dataclass(frozen=True)
class AuthResult:
    """A successful auth call: the user it identified, plus the raw Set-Cookie header value.

    auth sets the session cookie itself (auth/api/cookies.py) rather than handing web a raw
    token in the response body -- web proxies that header straight through to the browser
    rather than building its own cookie from scratch.
    """

    user: AuthUser
    set_cookie: str


class AuthClient(Protocol):
    async def signup(
        self, *, email: str, password: str, display_name: str, client_ip: str
    ) -> AuthResult: ...

    async def login(self, *, email: str, password: str, client_ip: str) -> AuthResult: ...

    async def validate_session(self, *, cookie_header: str) -> AuthResult: ...

    async def logout(self, *, cookie_header: str) -> None: ...
