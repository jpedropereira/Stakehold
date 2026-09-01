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

# auth's own exception handlers (auth/api/app.py) hard-code these `detail` strings for a bad
# password/email vs. an expired/unknown session -- both cases auth reports as a plain 401 with no
# other distinguishing field. auth's require_internal_secret dependency *also* answers with a 401
# on every route when X-Internal-Secret is missing or wrong, and its own detail text is different
# ("invalid or missing internal secret"). Matching on these strings is how web tells "the user got
# it wrong" apart from "web and auth's shared secret drifted" -- the latter must not be reported to
# the visitor as a bad password.
_INVALID_CREDENTIALS_DETAIL = "invalid email or password"
_SESSION_INVALID_DETAIL = "invalid or expired session"


def _detail(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("detail") if isinstance(body, dict) else None


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

    def __init__(self, client: httpx.AsyncClient, internal_secret: str):
        self._client = client
        self._internal_secret = internal_secret

    def _headers(self, **extra: str) -> dict[str, str]:
        # Cookie defaults to empty, not absent: self._client is one long-lived AsyncClient shared
        # by every concurrent visitor, and httpx's client-level cookie jar auto-attaches whatever
        # it last extracted from a Set-Cookie response header to the *next* outgoing request that
        # doesn't set its own Cookie header. Without this, a request racing right after another
        # visitor's login/signup response could carry that visitor's session cookie to auth.
        # Setting the header (even empty) makes http.cookiejar skip the jar for this request
        # (validate_session/logout override it below with the browser's real cookie).
        headers = {"X-Internal-Secret": self._internal_secret, "Cookie": ""}
        headers.update(extra)
        return headers

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AuthUnavailable(str(exc)) from exc

    def _parse_result(self, response: httpx.Response) -> AuthResult:
        set_cookie = response.headers.get("set-cookie")
        if set_cookie is None:
            raise AuthUnavailable("auth succeeded but set no session cookie")
        try:
            body = response.json()
            user = AuthUser(
                user_id=body["user_id"], email=body["email"], display_name=body["display_name"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            # ValueError covers json.JSONDecodeError; KeyError/TypeError cover a 2xx body that
            # parsed but is missing a field or isn't the object shape expected -- either way this
            # is auth misbehaving, not a normal error response, so it maps to the same
            # "can't reach the sign-in service" outcome as a network failure.
            raise AuthUnavailable(f"malformed auth response body: {exc}") from exc
        return AuthResult(user=user, set_cookie=set_cookie)

    async def signup(
        self, *, email: str, password: str, display_name: str, client_ip: str
    ) -> AuthResult:
        response = await self._request(
            "POST",
            "/signup",
            json={"email": email, "password": password, "display_name": display_name},
            headers=self._headers(**{"X-Forwarded-For": client_ip}),
        )
        if response.status_code == 201:
            return self._parse_result(response)
        if response.status_code == 409:
            raise EmailAlreadyRegistered(email)
        if response.status_code == 429:
            raise RateLimited("signup")
        if response.status_code == 422:
            raise InvalidRequest("signup")
        raise AuthUnavailable(f"unexpected auth response: {response.status_code}")

    async def login(self, *, email: str, password: str, client_ip: str) -> AuthResult:
        response = await self._request(
            "POST",
            "/login",
            json={"email": email, "password": password},
            headers=self._headers(**{"X-Forwarded-For": client_ip}),
        )
        if response.status_code == 200:
            return self._parse_result(response)
        if response.status_code == 401:
            if _detail(response) == _INVALID_CREDENTIALS_DETAIL:
                raise InvalidCredentials(email)
            raise AuthUnavailable("auth rejected the internal secret on /login")
        if response.status_code == 429:
            raise RateLimited("login")
        if response.status_code == 422:
            raise InvalidRequest("login")
        raise AuthUnavailable(f"unexpected auth response: {response.status_code}")

    async def validate_session(self, *, cookie_header: str) -> AuthResult:
        response = await self._request(
            "POST",
            "/sessions/validate",
            headers=self._headers(**{"Cookie": cookie_header}),
        )
        if response.status_code == 200:
            return self._parse_result(response)
        if response.status_code == 401:
            if _detail(response) == _SESSION_INVALID_DETAIL:
                raise SessionInvalid()
            raise AuthUnavailable("auth rejected the internal secret on /sessions/validate")
        raise AuthUnavailable(f"unexpected auth response: {response.status_code}")

    async def logout(self, *, cookie_header: str) -> None:
        response = await self._request(
            "POST",
            "/logout",
            headers=self._headers(**{"Cookie": cookie_header}),
        )
        # auth's /logout is idempotent -- 204 even for a missing or unrecognized token -- so any
        # other status means something is actually wrong with the auth service, not the session.
        if response.status_code != 204:
            raise AuthUnavailable(f"unexpected auth response: {response.status_code}")
