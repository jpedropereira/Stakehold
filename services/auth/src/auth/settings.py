import os
from dataclasses import dataclass

from dotenv import load_dotenv


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    internal_secret: str
    session_ttl_days: int
    session_absolute_ttl_days: int
    cookie_secure: bool
    api_host: str
    api_port: int
    pool_min_size: int
    pool_max_size: int


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        # Set-but-empty falls back to the default, matching _int_env. Reading it as False instead
        # would let `AUTH_COOKIE_SECURE=` (or an unset compose variable interpolating to nothing)
        # silently strip the session cookie's Secure flag in production.
        return default
    return raw.strip().lower() not in ("0", "false", "no")


def load_settings() -> Settings:
    load_dotenv()

    database_url = os.environ.get("AUTH_DATABASE_URL")
    if not database_url:
        raise SettingsError("AUTH_DATABASE_URL is not set")

    internal_secret = os.environ.get("AUTH_INTERNAL_SECRET")
    if not internal_secret:
        raise SettingsError("AUTH_INTERNAL_SECRET is not set")

    return Settings(
        database_url=database_url,
        internal_secret=internal_secret,
        session_ttl_days=_int_env("AUTH_SESSION_TTL_DAYS", 14),
        session_absolute_ttl_days=_int_env("AUTH_SESSION_ABSOLUTE_TTL_DAYS", 90),
        cookie_secure=_bool_env("AUTH_COOKIE_SECURE", True),
        api_host=os.environ.get("AUTH_API_HOST", "0.0.0.0"),
        api_port=_int_env("AUTH_API_PORT", 8001),
        pool_min_size=_int_env("AUTH_POOL_MIN_SIZE", 1),
        pool_max_size=_int_env("AUTH_POOL_MAX_SIZE", 10),
    )
