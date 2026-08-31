import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from web.api.csrf import CsrfInvalid, cookie_name, ensure_csrf_cookie, verify_csrf


def _build_app(*, cookie_secure: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/issue")
    def issue(request: Request, response: Response):
        token = ensure_csrf_cookie(request, response, cookie_secure=cookie_secure)
        return {"token": token}

    @app.post("/verify")
    def verify(request: Request, form_token: str | None = None):
        try:
            verify_csrf(request, form_token=form_token, cookie_secure=cookie_secure)
        except CsrfInvalid:
            return {"ok": False}
        return {"ok": True}

    return app


@pytest.fixture
def client():
    return TestClient(_build_app(cookie_secure=False))


def test_issues_a_cookie_when_none_exists(client):
    response = client.get("/issue")

    name = cookie_name(cookie_secure=False)
    assert name in response.cookies
    assert response.json()["token"] == response.cookies[name]


def test_reuses_an_existing_cookie(client):
    first = client.get("/issue").json()["token"]
    second = client.get("/issue").json()["token"]

    assert first == second


def test_matching_pair_passes(client):
    issued = client.get("/issue")
    token = issued.json()["token"]

    result = client.post("/verify", params={"form_token": token})

    assert result.json() == {"ok": True}


def test_mismatched_pair_is_rejected(client):
    client.get("/issue")

    result = client.post("/verify", params={"form_token": "not-the-real-token"})

    assert result.json() == {"ok": False}


def test_missing_form_token_is_rejected(client):
    client.get("/issue")

    result = client.post("/verify")

    assert result.json() == {"ok": False}


def test_missing_cookie_is_rejected():
    fresh_client = TestClient(_build_app(cookie_secure=False))

    result = fresh_client.post("/verify", params={"form_token": "anything"})

    assert result.json() == {"ok": False}


def test_non_ascii_token_is_rejected_not_a_500(client):
    client.get("/issue")

    result = client.post("/verify", params={"form_token": "ééé"})

    assert result.status_code == 200
    assert result.json() == {"ok": False}


def test_cookie_name_follows_cookie_secure_flag(client):
    secure_response = TestClient(_build_app(cookie_secure=True)).get("/issue")
    insecure_response = client.get("/issue")

    assert "__Host-stakehold_csrf" in secure_response.cookies
    assert "stakehold_csrf" in insecure_response.cookies
