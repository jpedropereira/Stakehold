import httpx
import pytest
from web.auth_client.errors import (
    AuthUnavailable,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRequest,
    RateLimited,
    SessionInvalid,
)
from web.auth_client.http_client import HttpAuthClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret


def _client(handler) -> HttpAuthClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://auth.internal", transport=transport)
    return HttpAuthClient(http_client, INTERNAL_SECRET)


async def test_signup_sends_internal_secret_and_forwarded_for():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.read()
        return httpx.Response(
            201,
            json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"},
            headers={"set-cookie": "stakehold_session=tok; Path=/"},
        )

    client = _client(handler)
    result = await client.signup(
        email="a@example.com",
        password="hunter2hunter2",  # pragma: allowlist secret
        display_name="Ann",
        client_ip="1.2.3.4",
    )

    assert captured["headers"]["x-internal-secret"] == INTERNAL_SECRET
    assert captured["headers"]["x-forwarded-for"] == "1.2.3.4"
    assert result.user.email == "a@example.com"
    assert result.set_cookie == "stakehold_session=tok; Path=/"


async def test_login_sends_internal_secret_and_forwarded_for():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"},
            headers={"set-cookie": "stakehold_session=tok; Path=/"},
        )

    client = _client(handler)
    result = await client.login(
        email="a@example.com",
        password="hunter2hunter2",  # pragma: allowlist secret
        client_ip="9.9.9.9",
    )

    assert captured["headers"]["x-internal-secret"] == INTERNAL_SECRET
    assert captured["headers"]["x-forwarded-for"] == "9.9.9.9"
    assert result.user.user_id == "u1"


async def test_login_401_with_credentials_detail_raises_invalid_credentials():
    client = _client(
        lambda request: httpx.Response(401, json={"detail": "invalid email or password"})
    )

    with pytest.raises(InvalidCredentials):
        await client.login(
            email="a@example.com",
            password="wrong",  # pragma: allowlist secret
            client_ip="1.1.1.1",
        )


async def test_login_401_with_other_detail_raises_auth_unavailable():
    # A 401 auth returns for a *different* reason -- e.g. a drifted internal secret -- must not
    # be reported to the visitor as "wrong password": require_internal_secret's own detail text
    # is different from the login-failure one.
    client = _client(
        lambda request: httpx.Response(401, json={"detail": "invalid or missing internal secret"})
    )

    with pytest.raises(AuthUnavailable):
        await client.login(
            email="a@example.com",
            password="wrong",  # pragma: allowlist secret
            client_ip="1.1.1.1",
        )


async def test_login_429_raises_rate_limited():
    client = _client(lambda request: httpx.Response(429, json={"detail": "slow down"}))

    with pytest.raises(RateLimited):
        await client.login(
            email="a@example.com",
            password="wrong",  # pragma: allowlist secret
            client_ip="1.1.1.1",
        )


async def test_login_422_raises_invalid_request():
    client = _client(lambda request: httpx.Response(422, json={"detail": []}))

    with pytest.raises(InvalidRequest):
        await client.login(
            email="a@example.com",
            password="wrong",  # pragma: allowlist secret
            client_ip="1.1.1.1",
        )


async def test_signup_409_raises_email_already_registered():
    client = _client(lambda request: httpx.Response(409, json={"detail": "taken"}))

    with pytest.raises(EmailAlreadyRegistered):
        await client.signup(
            email="a@example.com",
            password="hunter2hunter2",  # pragma: allowlist secret
            display_name="Ann",
            client_ip="1.1.1.1",
        )


async def test_validate_session_forwards_cookie_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"},
            headers={"set-cookie": "stakehold_session=renewed; Path=/"},
        )

    client = _client(handler)
    result = await client.validate_session(cookie_header="stakehold_session=tok")

    assert captured["headers"]["cookie"] == "stakehold_session=tok"
    assert result.set_cookie == "stakehold_session=renewed; Path=/"


async def test_validate_session_401_with_session_detail_raises_session_invalid():
    client = _client(
        lambda request: httpx.Response(401, json={"detail": "invalid or expired session"})
    )

    with pytest.raises(SessionInvalid):
        await client.validate_session(cookie_header="stakehold_session=tok")


async def test_validate_session_401_with_other_detail_raises_auth_unavailable():
    client = _client(
        lambda request: httpx.Response(401, json={"detail": "invalid or missing internal secret"})
    )

    with pytest.raises(AuthUnavailable):
        await client.validate_session(cookie_header="stakehold_session=tok")


async def test_logout_204_is_success():
    client = _client(lambda request: httpx.Response(204))

    await client.logout(cookie_header="stakehold_session=tok")  # must not raise


async def test_unexpected_status_raises_auth_unavailable():
    client = _client(lambda request: httpx.Response(500))

    with pytest.raises(AuthUnavailable):
        await client.login(email="a@example.com", password="x", client_ip="1.1.1.1")


async def test_connection_error_raises_auth_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)

    with pytest.raises(AuthUnavailable):
        await client.login(email="a@example.com", password="x", client_ip="1.1.1.1")


async def test_success_without_set_cookie_raises_auth_unavailable():
    client = _client(
        lambda request: httpx.Response(
            200, json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"}
        )
    )

    with pytest.raises(AuthUnavailable):
        await client.login(email="a@example.com", password="x", client_ip="1.1.1.1")


async def test_success_with_malformed_body_raises_auth_unavailable():
    # A 2xx with a body missing an expected field (schema drift, or a proxy injecting an
    # unrelated 200) must not crash the caller with a raw KeyError/JSONDecodeError.
    client = _client(
        lambda request: httpx.Response(
            200,
            json={"id": "u1"},  # missing user_id/email/display_name
            headers={"set-cookie": "stakehold_session=tok; Path=/"},
        )
    )

    with pytest.raises(AuthUnavailable):
        await client.login(email="a@example.com", password="x", client_ip="1.1.1.1")


async def test_success_with_non_json_body_raises_auth_unavailable():
    client = _client(
        lambda request: httpx.Response(
            200,
            content=b"<html>not json</html>",
            headers={"set-cookie": "stakehold_session=tok; Path=/"},
        )
    )

    with pytest.raises(AuthUnavailable):
        await client.login(email="a@example.com", password="x", client_ip="1.1.1.1")


async def test_no_cookie_leaks_from_one_call_into_the_next():
    # Regression test: the client is one long-lived AsyncClient shared across every visitor. A
    # prior response's Set-Cookie must never be replayed onto a later, unrelated request.
    captured_cookies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_cookies.append(request.headers.get("cookie"))
        if len(captured_cookies) == 1:
            return httpx.Response(
                200,
                json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"},
                headers={"set-cookie": "stakehold_session=USER_A_TOKEN; Path=/"},
            )
        return httpx.Response(
            201,
            json={"user_id": "u2", "email": "b@example.com", "display_name": "Bea"},
            headers={"set-cookie": "stakehold_session=USER_B_TOKEN; Path=/"},
        )

    client = _client(handler)

    await client.login(email="a@example.com", password="x", client_ip="1.1.1.1")
    await client.signup(
        email="b@example.com",
        password="y",  # pragma: allowlist secret
        display_name="Bea",
        client_ip="2.2.2.2",
    )

    assert captured_cookies[0] in (None, "")
    assert captured_cookies[1] in (None, ""), "user A's session cookie leaked into user B's call"
