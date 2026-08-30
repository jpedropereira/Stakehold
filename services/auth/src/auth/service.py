import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from auth import passwords, tokens
from auth.sessions import repository as sessions_repository
from auth.throttle import LoginThrottle
from auth.users import repository as users_repository

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Base for domain-level auth failures the API layer maps to HTTP responses."""


class EmailAlreadyRegistered(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


class SessionInvalid(AuthError):
    pass


class RateLimited(AuthError):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    id: uuid.UUID
    email: str
    display_name: str


@dataclass(frozen=True)
class Session:
    user: AuthenticatedUser
    token: str
    expires_at: datetime


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _create_session(
    conn, *, user_id: uuid.UUID, now: datetime, ttl_days: int, absolute_ttl_days: int
) -> tuple[str, datetime]:
    token = tokens.generate_token()
    expires_at = now + timedelta(days=ttl_days)
    absolute_expires_at = now + timedelta(days=absolute_ttl_days)
    sessions_repository.insert_session(
        conn,
        session_id=uuid.uuid7(),
        token_hash=tokens.hash_token(token),
        user_id=user_id,
        now=now,
        expires_at=expires_at,
        absolute_expires_at=absolute_expires_at,
    )
    return token, expires_at


def signup(
    conn,
    *,
    email: str,
    display_name: str,
    password: str,
    now: datetime,
    ttl_days: int,
    absolute_ttl_days: int,
) -> Session:
    email = _normalize_email(email)
    user_id = uuid.uuid7()
    password_hash = passwords.hash_password(password)
    try:
        users_repository.insert_user(
            conn,
            user_id=user_id,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise EmailAlreadyRegistered(email) from exc

    token, expires_at = _create_session(
        conn, user_id=user_id, now=now, ttl_days=ttl_days, absolute_ttl_days=absolute_ttl_days
    )
    logger.info("signup: new user registered.")
    return Session(
        user=AuthenticatedUser(id=user_id, email=email, display_name=display_name),
        token=token,
        expires_at=expires_at,
    )


def login(
    conn,
    *,
    email: str,
    password: str,
    client_ip: str,
    now: datetime,
    ttl_days: int,
    absolute_ttl_days: int,
    throttle: LoginThrottle,
) -> Session:
    email = _normalize_email(email)
    key = (email, client_ip)

    if throttle.is_locked_out(key):
        logger.warning("login: rate-limited.")
        raise RateLimited(email)

    user = users_repository.get_by_email(conn, email)
    if user is None:
        # Pays the same Argon2 cost a real verification would, so an unknown email isn't
        # distinguishable from a wrong password by response timing.
        passwords.dummy_verify()
        throttle.record_failure(key)
        raise InvalidCredentials(email)

    if not passwords.verify_password(user.password_hash, password):
        throttle.record_failure(key)
        raise InvalidCredentials(email)

    throttle.reset(key)
    token, expires_at = _create_session(
        conn, user_id=user.id, now=now, ttl_days=ttl_days, absolute_ttl_days=absolute_ttl_days
    )
    logger.info("login: session created.")
    return Session(
        user=AuthenticatedUser(id=user.id, email=user.email, display_name=user.display_name),
        token=token,
        expires_at=expires_at,
    )


def validate_session(conn, *, token: str, now: datetime, ttl_days: int) -> Session:
    token_hash = tokens.hash_token(token)
    new_expires_at = now + timedelta(days=ttl_days)
    result = sessions_repository.renew_and_fetch(
        conn, token_hash=token_hash, now=now, new_expires_at=new_expires_at
    )
    if result is None:
        raise SessionInvalid()

    return Session(
        user=AuthenticatedUser(
            id=result.user_id, email=result.email, display_name=result.display_name
        ),
        token=token,
        expires_at=result.expires_at,
    )


def logout(conn, *, token: str) -> None:
    sessions_repository.delete_by_token_hash(conn, tokens.hash_token(token))
    logger.info("logout: session removed.")
