import os
from dataclasses import dataclass

from auth.signing import SigningKey, SigningKeyError, load_signing_key
from dotenv import load_dotenv
from shared.env import bool_env, int_env


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
    signing_key: SigningKey
    delegated_token_ttl_seconds: int


def _load_signing_key() -> SigningKey:
    """From a mounted file (preferred in deployment) or an inline PEM (tests, CI). Exactly one:
    two sources could disagree, with no way to tell which one a running auth had used."""
    key_file = os.environ.get("AUTH_SIGNING_KEY_FILE")
    inline = os.environ.get("AUTH_SIGNING_KEY")
    if key_file and inline:
        raise SettingsError("set AUTH_SIGNING_KEY_FILE or AUTH_SIGNING_KEY, not both")
    if not key_file and not inline:
        raise SettingsError("AUTH_SIGNING_KEY_FILE (or AUTH_SIGNING_KEY) is not set")

    if key_file:
        try:
            with open(key_file, "rb") as handle:
                pem = handle.read()
        except OSError as exc:
            raise SettingsError(f"AUTH_SIGNING_KEY_FILE can't be read: {exc.strerror}") from exc
    else:
        pem = inline.encode("utf-8")

    try:
        return load_signing_key(pem)
    except SigningKeyError as exc:
        # SigningKeyError's message never includes the key material, so it's safe to surface.
        raise SettingsError(str(exc)) from exc


def load_settings() -> Settings:
    load_dotenv()

    database_url = os.environ.get("AUTH_DATABASE_URL")
    if not database_url:
        raise SettingsError("AUTH_DATABASE_URL is not set")

    internal_secret = os.environ.get("AUTH_INTERNAL_SECRET")
    if not internal_secret:
        raise SettingsError("AUTH_INTERNAL_SECRET is not set")

    delegated_token_ttl_seconds = int_env("AUTH_DELEGATED_TOKEN_TTL_SECONDS", 300)
    if delegated_token_ttl_seconds <= 0:
        raise SettingsError("AUTH_DELEGATED_TOKEN_TTL_SECONDS must be positive")

    return Settings(
        database_url=database_url,
        internal_secret=internal_secret,
        session_ttl_days=int_env("AUTH_SESSION_TTL_DAYS", 14),
        session_absolute_ttl_days=int_env("AUTH_SESSION_ABSOLUTE_TTL_DAYS", 90),
        cookie_secure=bool_env("AUTH_COOKIE_SECURE", True),
        api_host=os.environ.get("AUTH_API_HOST", "0.0.0.0"),
        api_port=int_env("AUTH_API_PORT", 8001),
        pool_min_size=int_env("AUTH_POOL_MIN_SIZE", 1),
        pool_max_size=int_env("AUTH_POOL_MAX_SIZE", 10),
        signing_key=_load_signing_key(),
        delegated_token_ttl_seconds=delegated_token_ttl_seconds,
    )
