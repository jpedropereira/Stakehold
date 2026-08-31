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

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret


def _client(handler) -> HttpAuthClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="http://auth.internal", transport=transport)
    return HttpAuthClient(http_client, INTERNAL_SECRET)


def test_signup_sends_internal_secret_and_forwarded_for():
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
    result = client.signup(
        email="a@example.com",
        password="hunter2hunter2",  # pragma: allowlist secret
        display_name="Ann",
        client_ip="1.2.3.4",
    )

    assert captured["headers"]["x-internal-secret"] == INTERNAL_SECRET
    assert captured["headers"]["x-forwarded-for"] == "1.2.3.4"
    assert result.user.email == "a@example.com"
    assert result.set_cookie == "stakehold_session=tok; Path=/"


def test_login_sends_internal_secret_and_forwarded_for():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"},
            headers={"set-cookie": "stakehold_session=tok; Path=/"},
        )

    client = _client(handler)
    result = client.login(
        email="a@example.com",
        password="hunter2hunter2",  # pragma: allowlist secret
        client_ip="9.9.9.9",
    )

    assert captured["headers"]["x-internal-secret"] == INTERNAL_SECRET
    assert captured["headers"]["x-forwarded-for"] == "9.9.9.9"
    assert result.user.user_id == "u1"


def test_login_401_raises_invalid_credentials():
    client = _client(lambda request: httpx.Response(401, json={"detail": "invalid"}))

    with pytest.raises(InvalidCredentials):
        client.login(
            email="a@example.com",
            password="wrong",  # pragma: allowlist secret
            client_ip="1.1.1.1",
        )


def test_login_429_raises_rate_limited():
    client = _client(lambda request: httpx.Response(429, json={"detail": "slow down"}))

    with pytest.raises(RateLimited):
        client.login(
            email="a@example.com",
            password="wrong",  # pragma: allowlist secret
            client_ip="1.1.1.1",
        )


def test_login_422_raises_invalid_request():
    client = _client(lambda request: httpx.Response(422, json={"detail": []}))

    with pytest.raises(InvalidRequest):
        client.login(
            email="a@example.com",
            password="wrong",  # pragma: allowlist secret
            client_ip="1.1.1.1",
        )


def test_signup_409_raises_email_already_registered():
    client = _client(lambda request: httpx.Response(409, json={"detail": "taken"}))

    with pytest.raises(EmailAlreadyRegistered):
        client.signup(
            email="a@example.com",
            password="hunter2hunter2",  # pragma: allowlist secret
            display_name="Ann",
            client_ip="1.1.1.1",
        )


def test_validate_session_forwards_cookie_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"},
            headers={"set-cookie": "stakehold_session=renewed; Path=/"},
        )

    client = _client(handler)
    result = client.validate_session(cookie_header="stakehold_session=tok")

    assert captured["headers"]["cookie"] == "stakehold_session=tok"
    assert result.set_cookie == "stakehold_session=renewed; Path=/"


def test_validate_session_401_raises_session_invalid():
    client = _client(lambda request: httpx.Response(401, json={"detail": "expired"}))

    with pytest.raises(SessionInvalid):
        client.validate_session(cookie_header="stakehold_session=tok")


def test_logout_204_is_success():
    client = _client(lambda request: httpx.Response(204))

    client.logout(cookie_header="stakehold_session=tok")  # must not raise


def test_unexpected_status_raises_auth_unavailable():
    client = _client(lambda request: httpx.Response(500))

    with pytest.raises(AuthUnavailable):
        client.login(email="a@example.com", password="x", client_ip="1.1.1.1")


def test_connection_error_raises_auth_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)

    with pytest.raises(AuthUnavailable):
        client.login(email="a@example.com", password="x", client_ip="1.1.1.1")


def test_success_without_set_cookie_raises_auth_unavailable():
    client = _client(
        lambda request: httpx.Response(
            200, json={"user_id": "u1", "email": "a@example.com", "display_name": "Ann"}
        )
    )

    with pytest.raises(AuthUnavailable):
        client.login(email="a@example.com", password="x", client_ip="1.1.1.1")
