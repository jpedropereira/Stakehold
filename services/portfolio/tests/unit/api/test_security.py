"""require_internal_secret tested by calling it directly, data-store's own precedent
(tests/unit/api/test_security.py) -- no HTTP layer needed to exercise it."""

import pytest
from fastapi import HTTPException
from portfolio.api.security import require_internal_secret


def _settings(secret):
    return type("S", (), {"api_internal_secret": secret})()


def test_accepts_a_matching_secret():
    require_internal_secret(
        settings=_settings("s3cret"),  # pragma: allowlist secret
        x_internal_secret="s3cret",  # pragma: allowlist secret
    )


def test_rejects_a_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(
            settings=_settings("s3cret"), x_internal_secret=None  # pragma: allowlist secret
        )

    assert exc_info.value.status_code == 401


def test_rejects_a_wrong_secret():
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(
            settings=_settings("s3cret"),  # pragma: allowlist secret
            x_internal_secret="nope",  # pragma: allowlist secret
        )

    assert exc_info.value.status_code == 401


def test_rejects_when_no_secret_is_configured():
    """A deployment that came up without PORTFOLIO_INTERNAL_SECRET set is a misconfiguration,
    not an open endpoint -- data-store's own require_internal_secret makes the same call."""
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(settings=_settings(None), x_internal_secret="anything")

    assert exc_info.value.status_code == 500


def test_a_non_ascii_header_is_rejected_with_401_not_a_500():
    """secrets.compare_digest raises TypeError for a str containing non-ASCII characters --
    comparing as UTF-8 bytes (not str) is what turns a hostile header into a clean 401 instead
    of a 500. Same regression auth's and data-store's own security tests guard against."""
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(
            settings=_settings("s3cret"),  # pragma: allowlist secret
            x_internal_secret="caf\xe9",  # pragma: allowlist secret
        )

    assert exc_info.value.status_code == 401
