from datetime import UTC, datetime, timedelta

import pytest
from auth import passwords, service, tokens
from auth.throttle import LoginThrottle

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TTL_DAYS = 14
ABSOLUTE_TTL_DAYS = 90


def _signup(**overrides):
    kwargs = {
        "conn": None,
        "email": "person@example.com",
        "display_name": "Person",
        "password": "correct horse battery staple",
        "now": NOW,
        "ttl_days": TTL_DAYS,
        "absolute_ttl_days": ABSOLUTE_TTL_DAYS,
    }
    kwargs.update(overrides)
    return service.signup(**kwargs)


def test_signup_creates_user_and_returns_a_session(repos):
    session = _signup()

    assert session.user.email == "person@example.com"
    assert session.user.display_name == "Person"
    assert session.token
    assert session.expires_at == NOW + timedelta(days=TTL_DAYS)


def test_signup_normalizes_email_case_and_whitespace(repos):
    session = _signup(email="  Person@Example.COM  ")

    assert session.user.email == "person@example.com"


def test_signup_stores_a_hashed_password_not_the_plaintext(repos):
    users_repo, _ = repos

    _signup(password="hunter2hunter2")

    stored = users_repo.by_email["person@example.com"]
    assert stored.password_hash != "hunter2hunter2"
    assert passwords.verify_password(stored.password_hash, "hunter2hunter2") is True


def test_signup_duplicate_email_raises(repos):
    _signup()

    with pytest.raises(service.EmailAlreadyRegistered):
        _signup(display_name="Someone Else")


def test_login_succeeds_with_correct_password(repos):
    _signup(password="correct horse battery staple")
    throttle = LoginThrottle()

    session = service.login(
        None,
        email="person@example.com",
        password="correct horse battery staple",
        client_ip="1.2.3.4",
        now=NOW,
        ttl_days=TTL_DAYS,
        absolute_ttl_days=ABSOLUTE_TTL_DAYS,
        throttle=throttle,
    )

    assert session.user.email == "person@example.com"
    assert session.token


def test_login_wrong_password_raises_invalid_credentials(repos):
    _signup(password="correct horse battery staple")
    throttle = LoginThrottle()

    with pytest.raises(service.InvalidCredentials):
        service.login(
            None,
            email="person@example.com",
            password="wrong password",
            client_ip="1.2.3.4",
            now=NOW,
            ttl_days=TTL_DAYS,
            absolute_ttl_days=ABSOLUTE_TTL_DAYS,
            throttle=throttle,
        )


def test_login_unknown_email_raises_invalid_credentials_not_a_different_error(repos):
    throttle = LoginThrottle()

    with pytest.raises(service.InvalidCredentials):
        service.login(
            None,
            email="nobody@example.com",
            password="whatever12345",
            client_ip="1.2.3.4",
            now=NOW,
            ttl_days=TTL_DAYS,
            absolute_ttl_days=ABSOLUTE_TTL_DAYS,
            throttle=throttle,
        )


def test_login_records_failures_and_then_rate_limits(repos):
    _signup(password="correct horse battery staple")
    throttle = LoginThrottle(max_attempts=3)

    for _ in range(3):
        with pytest.raises(service.InvalidCredentials):
            service.login(
                None,
                email="person@example.com",
                password="wrong password",
                client_ip="1.2.3.4",
                now=NOW,
                ttl_days=TTL_DAYS,
                absolute_ttl_days=ABSOLUTE_TTL_DAYS,
                throttle=throttle,
            )

    with pytest.raises(service.RateLimited):
        service.login(
            None,
            email="person@example.com",
            password="correct horse battery staple",
            client_ip="1.2.3.4",
            now=NOW,
            ttl_days=TTL_DAYS,
            absolute_ttl_days=ABSOLUTE_TTL_DAYS,
            throttle=throttle,
        )


def test_login_success_resets_the_throttle(repos):
    _signup(password="correct horse battery staple")
    throttle = LoginThrottle(max_attempts=3)

    with pytest.raises(service.InvalidCredentials):
        service.login(
            None,
            email="person@example.com",
            password="wrong password",
            client_ip="1.2.3.4",
            now=NOW,
            ttl_days=TTL_DAYS,
            absolute_ttl_days=ABSOLUTE_TTL_DAYS,
            throttle=throttle,
        )

    service.login(
        None,
        email="person@example.com",
        password="correct horse battery staple",
        client_ip="1.2.3.4",
        now=NOW,
        ttl_days=TTL_DAYS,
        absolute_ttl_days=ABSOLUTE_TTL_DAYS,
        throttle=throttle,
    )

    assert throttle.is_locked_out(("person@example.com", "1.2.3.4")) is False


def test_validate_session_returns_the_user_and_slides_expiry(repos):
    session = _signup()
    later = NOW + timedelta(days=1)

    result = service.validate_session(None, token=session.token, now=later, ttl_days=TTL_DAYS)

    assert result.user.email == "person@example.com"
    assert result.expires_at == later + timedelta(days=TTL_DAYS)


def test_validate_session_unknown_token_raises(repos):
    with pytest.raises(service.SessionInvalid):
        service.validate_session(None, token="not-a-real-token", now=NOW, ttl_days=TTL_DAYS)


def test_validate_session_after_sliding_expiry_raises(repos):
    session = _signup()
    after_expiry = NOW + timedelta(days=TTL_DAYS, seconds=1)

    with pytest.raises(service.SessionInvalid):
        service.validate_session(None, token=session.token, now=after_expiry, ttl_days=TTL_DAYS)


def test_validate_session_renewal_never_exceeds_absolute_expiry(repos):
    _, sessions_repo = repos
    session = _signup()
    # Simulate a session that's already been renewed close to its absolute cap -- a single
    # sliding TTL (14 days) can't reach the 90-day cap from a fresh signup, so this stands in
    # for the many renewal cycles that would get it there.
    still_valid_expires_at = NOW + timedelta(days=ABSOLUTE_TTL_DAYS - 1)
    sessions_repo.by_token_hash[tokens.hash_token(session.token)][
        "expires_at"
    ] = still_valid_expires_at
    now = NOW + timedelta(days=ABSOLUTE_TTL_DAYS - 2)

    result = service.validate_session(None, token=session.token, now=now, ttl_days=TTL_DAYS)

    assert result.expires_at == NOW + timedelta(days=ABSOLUTE_TTL_DAYS)


def test_logout_removes_the_session(repos):
    session = _signup()

    service.logout(None, token=session.token)

    with pytest.raises(service.SessionInvalid):
        service.validate_session(None, token=session.token, now=NOW, ttl_days=TTL_DAYS)


def test_logout_is_idempotent_for_an_unknown_token(repos):
    service.logout(None, token="never-issued-token")
