"""Typed environment-variable readers, shared by every service's `settings.py`.

Extracted after `auth` and `web` each carried a byte-for-byte copy of the same three functions
(`_int_env`, `_float_env`, `_bool_env`) -- the same "extracted on the second occurrence" pattern
`db/connection.py` and `logging_config.py` followed (see docs/architecture.md).
"""

import os


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        # Set-but-empty falls back to the default, matching int_env/float_env. Reading it as
        # False instead would let e.g. `WEB_COOKIE_SECURE=` (or an unset compose variable
        # interpolating to nothing) silently strip a Secure flag in production.
        return default
    return raw.strip().lower() not in ("0", "false", "no")
