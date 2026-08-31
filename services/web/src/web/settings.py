import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# services/web/src/web/settings.py -> repo root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_BRAND_DIR = _REPO_ROOT / "brand"


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    auth_api_url: str
    internal_secret: str
    brand_dir: Path
    cookie_secure: bool
    api_host: str
    api_port: int
    http_timeout_seconds: float


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        # Set-but-empty falls back to the default, matching _int_env. Reading it as False instead
        # would let `WEB_COOKIE_SECURE=` (or an unset compose variable interpolating to nothing)
        # silently strip the Secure flag on the CSRF and session cookies in production.
        return default
    return raw.strip().lower() not in ("0", "false", "no")


def load_settings() -> Settings:
    load_dotenv()

    auth_api_url = os.environ.get("WEB_AUTH_API_URL")
    if not auth_api_url:
        raise SettingsError("WEB_AUTH_API_URL is not set")

    internal_secret = os.environ.get("WEB_INTERNAL_SECRET")
    if not internal_secret:
        raise SettingsError("WEB_INTERNAL_SECRET is not set")

    brand_dir_raw = os.environ.get("WEB_BRAND_DIR")
    brand_dir = Path(brand_dir_raw) if brand_dir_raw else _DEFAULT_BRAND_DIR

    return Settings(
        auth_api_url=auth_api_url.rstrip("/"),
        internal_secret=internal_secret,
        brand_dir=brand_dir,
        cookie_secure=_bool_env("WEB_COOKIE_SECURE", True),
        api_host=os.environ.get("WEB_API_HOST", "0.0.0.0"),
        api_port=_int_env("WEB_API_PORT", 8000),
        http_timeout_seconds=_float_env("WEB_HTTP_TIMEOUT_SECONDS", 10.0),
    )
