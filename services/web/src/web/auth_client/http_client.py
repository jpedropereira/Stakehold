import httpx

from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import (
    AuthUnavailable,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRequest,
    RateLimited,
    SessionInvalid,
)


def _user_from_body(body: dict) -> AuthUser:
    return AuthUser(user_id=body["user_id"], email=body["email"], display_name=body["display_name"])


class HttpAuthClient:
    """The real AuthClient, calling auth's identity API (task 2).

    Every call sets X-Internal-Secret -- auth rejects every route but /health without it.
    signup/login also set X-Forwarded-For to the browser's address: auth's login/signup
    throttles key on (subject, client_ip), and without this header every request looks like it
    came from web's own container address, collapsing both throttles to one shared bucket (see
    auth/docs/architecture.md, "Login throttling and user enumeration"). validate_session/logout
    forward the browser's own Cookie header so auth can read the session token -- web never
    parses or reissues that token itself.
    """

    def __init__(self, client: httpx.Client, internal_secret: str):
        self._client = client
        self._internal_secret = internal_secret

    def _headers(self, **extra: str) -> dict[str, str]:
        headers = {"X-Internal-Secret": self._internal_secret}
        headers.update(extra)
        return headers

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AuthUnavailable(str(exc)) from exc

    def signup(
        self, *, email: str, password: str, display_name: str, client_ip: str
    ) -> AuthResult:
        response = self._request(
            "POST",
            "/signup",
            json={"email": email, "password": password, "display_name": display_name},
            headers=self._headers(**{"X-Forwarded-For": client_ip}),
        )
        if response.status_code == 201:
            set_cookie = response.headers.get("set-cookie")
            if set_cookie is None:
                raise AuthUnavailable("auth signup succeeded but set no session cookie")
            return AuthResult(user=_user_from_body(response.json()), set_cookie=set_cookie)
        if response.status_code == 409:
            raise EmailAlreadyRegistered(email)
        if response.status_code == 429:
            raise RateLimited("signup")
        if response.status_code == 422:
            raise InvalidRequest("signup")
        raise AuthUnavailable(f"unexpected auth response: {response.status_code}")

    def login(self, *, email: str, password: str, client_ip: str) -> AuthResult:
        response = self._request(
            "POST",
            "/login",
            json={"email": email, "password": password},
            headers=self._headers(**{"X-Forwarded-For": client_ip}),
        )
        if response.status_code == 200:
            set_cookie = response.headers.get("set-cookie")
            if set_cookie is None:
                raise AuthUnavailable("auth login succeeded but set no session cookie")
            return AuthResult(user=_user_from_body(response.json()), set_cookie=set_cookie)
        if response.status_code == 401:
            raise InvalidCredentials(email)
        if response.status_code == 429:
            raise RateLimited("login")
        if response.status_code == 422:
            raise InvalidRequest("login")
        raise AuthUnavailable(f"unexpected auth response: {response.status_code}")

    def validate_session(self, *, cookie_header: str) -> AuthResult:
        response = self._request(
            "POST",
            "/sessions/validate",
            headers=self._headers(**{"Cookie": cookie_header}),
        )
        if response.status_code == 200:
            set_cookie = response.headers.get("set-cookie")
            if set_cookie is None:
                raise AuthUnavailable("auth validate succeeded but set no session cookie")
            return AuthResult(user=_user_from_body(response.json()), set_cookie=set_cookie)
        if response.status_code == 401:
            raise SessionInvalid()
        raise AuthUnavailable(f"unexpected auth response: {response.status_code}")

    def logout(self, *, cookie_header: str) -> None:
        response = self._request(
            "POST",
            "/logout",
            headers=self._headers(**{"Cookie": cookie_header}),
        )
        # auth's /logout is idempotent -- 204 even for a missing or unrecognized token -- so any
        # other status means something is actually wrong with the auth service, not the session.
        if response.status_code != 204:
            raise AuthUnavailable(f"unexpected auth response: {response.status_code}")
