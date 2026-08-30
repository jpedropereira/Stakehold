import pytest
from data_store.api.security import require_internal_secret
from data_store.settings import Settings
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def make_settings(secret):
    return Settings(
        database_url="postgresql://user:pass@localhost/db",
        config_path=None,
        tracked_tickers_path=None,
        exchange_suffixes_path=None,
        regimes_csv_path=None,
        currencies_path=None,
        internal_api_secret=secret,
    )


def creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_accepts_the_correct_secret():
    require_internal_secret(credentials=creds("s3cret"), settings=make_settings("s3cret"))


def test_rejects_a_wrong_secret():
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(credentials=creds("wrong"), settings=make_settings("s3cret"))

    assert exc_info.value.status_code == 401


def test_rejects_a_missing_credential():
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(credentials=None, settings=make_settings("s3cret"))

    assert exc_info.value.status_code == 401


def test_error_body_never_echoes_the_configured_secret():
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(credentials=creds("wrong"), settings=make_settings("s3cret"))

    assert "s3cret" not in str(exc_info.value.detail)


def test_fails_loudly_when_the_server_has_no_secret_configured():
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(credentials=creds("anything"), settings=make_settings(None))

    assert exc_info.value.status_code == 500


def test_rejects_a_non_ascii_token_with_401_rather_than_crashing():
    """`compare_digest` raises TypeError on a non-ASCII str, and headers decode as latin-1, so
    an unauthenticated caller could turn a garbage token into a 500."""
    with pytest.raises(HTTPException) as exc_info:
        require_internal_secret(credentials=creds("s3crét"), settings=make_settings("s3cret"))

    assert exc_info.value.status_code == 401


def test_accepts_a_non_ascii_secret_the_caller_gets_right():
    require_internal_secret(credentials=creds("pässwörd"), settings=make_settings("pässwörd"))
