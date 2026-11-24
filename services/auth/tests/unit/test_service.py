import logging
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from auth import passwords, service, tokens
from auth.signing import load_signing_key
from auth.throttle import AttemptThrottle
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TTL_DAYS = 14
ABSOLUTE_TTL_DAYS = 90
CLIENT_IP = "1.2.3.4"


def _signup(conn, **overrides):
    kwargs = {
        "conn": conn,
        "email": "person@example.com",
        "display_name": "Person",
        "password": "correct horse battery staple",  # pragma: allowlist secret
        "client_ip": CLIENT_IP,
        "now": NOW,
        "ttl_days": TTL_DAYS,
        "absolute_ttl_days": ABSOLUTE_TTL_DAYS,
        "throttle": AttemptThrottle(),
    }
    kwargs.update(overrides)
    return service.signup(**kwargs)


def _login(conn, **overrides):
    kwargs = {
        "email": "person@example.com",
        "password": "correct horse battery staple",  # pragma: allowlist secret
        "client_ip": CLIENT_IP,
        "now": NOW,
        "ttl_days": TTL_DAYS,
        "absolute_ttl_days": ABSOLUTE_TTL_DAYS,
        "throttle": AttemptThrottle(),
    }
    kwargs.update(overrides)
    return service.login(conn, **kwargs)


def test_signup_creates_user_and_returns_a_session(repos, conn):
    session = _signup(conn)

    assert session.user.email == "person@example.com"
    assert session.user.display_name == "Person"
    assert session.token
    assert session.expires_at == NOW + timedelta(days=TTL_DAYS)


def test_signup_normalizes_email_case_and_whitespace(repos, conn):
    session = _signup(conn, email="  Person@Example.COM  ")

    assert session.user.email == "person@example.com"


def test_signup_stores_a_hashed_password_not_the_plaintext(repos, conn):
    users_repo, _ = repos

    _signup(conn, password="hunter2hunter2")  # pragma: allowlist secret

    stored = users_repo.by_email["person@example.com"]
    assert stored.password_hash != "hunter2hunter2"  # pragma: allowlist secret
    assert passwords.verify_password(stored.password_hash, "hunter2hunter2") is True


def test_signup_duplicate_email_raises(repos, conn):
    _signup(conn)

    with pytest.raises(service.EmailAlreadyRegistered):
        _signup(conn, display_name="Someone Else")


def test_signup_commits_the_user_and_the_session_in_one_transaction(repos, conn):
    _signup(conn)

    # Two commits would mean the user is durable before the session is: a failure in between
    # leaves a committed account whose owner can never sign up again (409) and was never logged in.
    assert conn.commits == 1


def test_signup_rolls_back_the_user_when_creating_the_session_fails(repos, conn, monkeypatch):
    users_repo, sessions_repo = repos

    def boom(*args, **kwargs):
        raise RuntimeError("session insert failed")

    monkeypatch.setattr(sessions_repo, "insert_session", boom)

    with pytest.raises(RuntimeError):
        _signup(conn)

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_signup_duplicate_email_rolls_back_and_leaves_the_connection_usable(repos, conn):
    _signup(conn)
    commits_after_first = conn.commits

    with pytest.raises(service.EmailAlreadyRegistered):
        _signup(conn, display_name="Someone Else")

    assert conn.commits == commits_after_first
    assert conn.rollbacks == 1


def test_signup_rate_limits_once_the_attempt_limit_is_reached(repos, conn):
    throttle = AttemptThrottle(max_attempts=2)

    _signup(conn, email="a@example.com", throttle=throttle)
    _signup(conn, email="b@example.com", throttle=throttle)

    with pytest.raises(service.RateLimited):
        _signup(conn, email="c@example.com", throttle=throttle)


def test_signup_counts_successful_attempts_not_only_failures(repos, conn):
    # The Argon2 cost is paid by a successful signup too, so counting only duplicates/failures
    # would leave the CPU/memory exhaustion vector wide open.
    throttle = AttemptThrottle(max_attempts=1)

    _signup(conn, email="a@example.com", throttle=throttle)

    assert throttle.is_locked_out((service.SIGNUP_THROTTLE_SUBJECT, CLIENT_IP)) is True


def test_signup_throttle_is_per_client_ip(repos, conn):
    throttle = AttemptThrottle(max_attempts=1)

    _signup(conn, email="a@example.com", client_ip="1.1.1.1", throttle=throttle)

    session = _signup(conn, email="b@example.com", client_ip="2.2.2.2", throttle=throttle)
    assert session.user.email == "b@example.com"


def test_login_succeeds_with_correct_password(repos, conn):
    _signup(conn)

    session = _login(conn)

    assert session.user.email == "person@example.com"
    assert session.token


def test_login_wrong_password_raises_invalid_credentials(repos, conn):
    _signup(conn)

    with pytest.raises(service.InvalidCredentials):
        _login(conn, password="wrong password")  # pragma: allowlist secret


def test_login_unknown_email_raises_invalid_credentials_not_a_different_error(repos, conn):
    with pytest.raises(service.InvalidCredentials):
        _login(
            conn,
            email="nobody@example.com",
            password="whatever12345",  # pragma: allowlist secret
        )


def test_login_with_a_corrupt_stored_hash_is_invalid_credentials_not_a_crash(repos, conn):
    users_repo, _ = repos
    _signup(conn)
    stored = users_repo.by_email["person@example.com"]
    users_repo.by_email["person@example.com"] = type(stored)(
        id=stored.id,
        email=stored.email,
        display_name=stored.display_name,
        password_hash="corrupt",  # pragma: allowlist secret
        created_at=stored.created_at,
        is_admin=stored.is_admin,
    )
    throttle = AttemptThrottle()

    with pytest.raises(service.InvalidCredentials):
        _login(conn, throttle=throttle)

    assert throttle.is_locked_out(("person@example.com", CLIENT_IP)) is False


def test_login_records_failures_and_then_rate_limits(repos, conn):
    _signup(conn)
    throttle = AttemptThrottle(max_attempts=3)

    for _ in range(3):
        with pytest.raises(service.InvalidCredentials):
            _login(conn, password="wrong password", throttle=throttle)  # pragma: allowlist secret

    with pytest.raises(service.RateLimited):
        _login(conn, throttle=throttle)


def test_login_success_resets_the_throttle(repos, conn):
    _signup(conn)
    throttle = AttemptThrottle(max_attempts=3)

    with pytest.raises(service.InvalidCredentials):
        _login(conn, password="wrong password", throttle=throttle)  # pragma: allowlist secret

    _login(conn, throttle=throttle)

    assert throttle.is_locked_out(("person@example.com", CLIENT_IP)) is False


def test_validate_session_returns_the_user_and_slides_expiry(repos, conn):
    session = _signup(conn)
    later = NOW + timedelta(days=1)

    result = service.validate_session(conn, token=session.token, now=later, ttl_days=TTL_DAYS)

    assert result.user.email == "person@example.com"
    assert result.expires_at == later + timedelta(days=TTL_DAYS)


def test_validate_session_unknown_token_raises(repos, conn):
    with pytest.raises(service.SessionInvalid):
        service.validate_session(conn, token="not-a-real-token", now=NOW, ttl_days=TTL_DAYS)


def test_validate_session_after_sliding_expiry_raises(repos, conn):
    session = _signup(conn)
    after_expiry = NOW + timedelta(days=TTL_DAYS, seconds=1)

    with pytest.raises(service.SessionInvalid):
        service.validate_session(conn, token=session.token, now=after_expiry, ttl_days=TTL_DAYS)


def test_validate_session_renewal_never_exceeds_absolute_expiry(repos, conn):
    _, sessions_repo = repos
    session = _signup(conn)
    # Simulate a session that's already been renewed close to its absolute cap -- a single
    # sliding TTL (14 days) can't reach the 90-day cap from a fresh signup, so this stands in
    # for the many renewal cycles that would get it there.
    still_valid_expires_at = NOW + timedelta(days=ABSOLUTE_TTL_DAYS - 1)
    sessions_repo.by_token_hash[tokens.hash_token(session.token)][
        "expires_at"
    ] = still_valid_expires_at
    now = NOW + timedelta(days=ABSOLUTE_TTL_DAYS - 2)

    result = service.validate_session(conn, token=session.token, now=now, ttl_days=TTL_DAYS)

    assert result.expires_at == NOW + timedelta(days=ABSOLUTE_TTL_DAYS)


def test_logout_removes_the_session(repos, conn):
    session = _signup(conn)

    service.logout(conn, token=session.token)

    with pytest.raises(service.SessionInvalid):
        service.validate_session(conn, token=session.token, now=NOW, ttl_days=TTL_DAYS)


def test_logout_is_idempotent_for_an_unknown_token(repos, conn):
    service.logout(conn, token="never-issued-token")


def test_a_new_signup_is_not_an_admin(repos, conn):
    session = _signup(conn)

    assert session.user.is_admin is False


def test_set_admin_grants_admin_and_commits(repos, conn):
    _signup(conn)
    commits_before = conn.commits

    assert service.set_admin(conn, email="person@example.com", is_admin=True) is True

    users_repo, _ = repos
    assert users_repo.by_email["person@example.com"].is_admin is True
    assert conn.commits == commits_before + 1


def test_set_admin_normalizes_the_email_like_login_does(repos, conn):
    _signup(conn)

    assert service.set_admin(conn, email="  Person@Example.COM ", is_admin=True) is True


def test_set_admin_returns_false_for_an_unknown_email(repos, conn):
    assert service.set_admin(conn, email="nobody@example.com", is_admin=True) is False


def test_login_reports_is_admin(repos, conn):
    _signup(conn)
    service.set_admin(conn, email="person@example.com", is_admin=True)

    session = _login(conn)

    assert session.user.is_admin is True


def test_validate_session_reports_is_admin(repos, conn):
    session = _signup(conn)
    service.set_admin(conn, email="person@example.com", is_admin=True)

    validated = service.validate_session(conn, token=session.token, now=NOW, ttl_days=TTL_DAYS)

    assert validated.user.is_admin is True


def test_revoking_admin_takes_effect_on_the_next_validation(repos, conn):
    session = _signup(conn)
    service.set_admin(conn, email="person@example.com", is_admin=True)
    service.validate_session(conn, token=session.token, now=NOW, ttl_days=TTL_DAYS)

    service.set_admin(conn, email="person@example.com", is_admin=False)
    validated = service.validate_session(conn, token=session.token, now=NOW, ttl_days=TTL_DAYS)

    assert validated.user.is_admin is False


# --- delegated tokens ---------------------------------------------------------

TOKEN_TTL_SECONDS = 300


def _signing_key():
    # Generated per run, never committed.
    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return load_signing_key(pem)


def _delegate(conn, session_token, *, key, now=NOW):
    return service.issue_delegated_token(
        conn,
        session_token=session_token,
        now=now,
        ttl_days=TTL_DAYS,
        signing_key=key,
        token_ttl_seconds=TOKEN_TTL_SECONDS,
    )


def _claims(token, key, *, at=NOW):
    leeway = (datetime.now(UTC) - at).total_seconds()
    return jwt.decode(
        token,
        key.private_key.public_key(),
        algorithms=["EdDSA"],
        audience="portfolio",
        leeway=leeway,
    )


def test_delegated_token_names_the_session_user_as_subject(repos, conn):
    key = _signing_key()
    session = _signup(conn)

    delegated = _delegate(conn, session.token, key=key)

    assert _claims(delegated.token, key)["sub"] == str(session.user.id)
    assert delegated.expires_in == TOKEN_TTL_SECONDS


def test_delegated_token_for_an_unknown_session_raises(repos, conn):
    with pytest.raises(service.SessionInvalid):
        _delegate(conn, "not-a-real-token", key=_signing_key())


def test_delegated_token_for_an_expired_session_raises(repos, conn):
    session = _signup(conn)
    after_expiry = NOW + timedelta(days=TTL_DAYS, seconds=1)

    with pytest.raises(service.SessionInvalid):
        _delegate(conn, session.token, key=_signing_key(), now=after_expiry)


def test_delegated_token_for_a_logged_out_session_raises(repos, conn):
    session = _signup(conn)
    service.logout(conn, token=session.token)

    with pytest.raises(service.SessionInvalid):
        _delegate(conn, session.token, key=_signing_key())


def test_delegated_token_is_logged_by_jti_never_by_value(repos, conn, caplog):
    key = _signing_key()
    session = _signup(conn)

    with caplog.at_level(logging.DEBUG):
        delegated = _delegate(conn, session.token, key=key)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert delegated.jti in logged
    assert delegated.token not in logged
    assert session.token not in logged
    assert str(session.user.id) not in logged
