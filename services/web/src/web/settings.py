import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from shared.env import bool_env, float_env, int_env

# services/web/src/web/settings.py -> repo root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_BRAND_DIR = _REPO_ROOT / "brand"


class SettingsError(Exception):
    """Raised when required configuration is missing from the environment."""


@dataclass(frozen=True)
class Settings:
    auth_api_url: str
    internal_secret: str
    portfolio_api_url: str
    portfolio_internal_secret: str
    brand_dir: Path
    cookie_secure: bool
    api_host: str
    api_port: int
    http_timeout_seconds: float


def load_settings() -> Settings:
    load_dotenv()

    auth_api_url = os.environ.get("WEB_AUTH_API_URL")
    if not auth_api_url:
        raise SettingsError("WEB_AUTH_API_URL is not set")

    internal_secret = os.environ.get("WEB_INTERNAL_SECRET")
    if not internal_secret:
        raise SettingsError("WEB_INTERNAL_SECRET is not set")

    portfolio_api_url = os.environ.get("WEB_PORTFOLIO_API_URL")
    if not portfolio_api_url:
        raise SettingsError("WEB_PORTFOLIO_API_URL is not set")

    # Deliberately a separate, WEB_-prefixed var from WEB_INTERNAL_SECRET above (auth's own
    # credential) -- must equal portfolio's own PORTFOLIO_INTERNAL_SECRET, the same
    # "each edge rotates independently" reasoning portfolio/settings.py documents for its own
    # two secrets.
    portfolio_internal_secret = os.environ.get("WEB_PORTFOLIO_INTERNAL_SECRET")
    if not portfolio_internal_secret:
        raise SettingsError("WEB_PORTFOLIO_INTERNAL_SECRET is not set")

    brand_dir_raw = os.environ.get("WEB_BRAND_DIR")
    brand_dir = Path(brand_dir_raw) if brand_dir_raw else _DEFAULT_BRAND_DIR

    return Settings(
        auth_api_url=auth_api_url.rstrip("/"),
        internal_secret=internal_secret,
        portfolio_api_url=portfolio_api_url.rstrip("/"),
        portfolio_internal_secret=portfolio_internal_secret,
        brand_dir=brand_dir,
        cookie_secure=bool_env("WEB_COOKIE_SECURE", True),
        api_host=os.environ.get("WEB_API_HOST", "0.0.0.0"),
        api_port=int_env("WEB_API_PORT", 8000),
        http_timeout_seconds=float_env("WEB_HTTP_TIMEOUT_SECONDS", 10.0),
    )
