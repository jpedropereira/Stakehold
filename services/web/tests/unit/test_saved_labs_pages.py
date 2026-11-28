"""Saved labs (LF-1) in web: the list and save form on /labs, POST /labs/saved (save, a taken
name then Replace, a refused lab, an outage), and the delete's confirmation page and POST.

Carries its own minimal fakes, like test_labs_page.py: only the calls these pages make. The
backtest isn't under test here, so it always answers with nothing to show.
"""

import re
from decimal import Decimal
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    Holding,
    Holdings,
    SavedLab,
    SavedLabSaveResult,
)
from web.portfolio_client.errors import (
    InvalidPortfolioRequest,
    PortfolioUnavailable,
    SavedLabNameTaken,
    SavedLabNotFound,
)
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
CSRF_TOKEN = "csrf-tok"
CORE = SavedLab(name="Core", assets=(("IWDA.AS", "60"), ("AAPL", "40")), updated_at="2026-11-02")
TECH = SavedLab(name="Tech", assets=(("AAPL", "100"),), updated_at="2026-11-01")


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.holdings = Holdings(holdings=(), totals=None)
        self.tickers = ["IWDA.AS", "VWRL.AS", "AAPL"]
        self.saved_labs: list[SavedLab] = []
        self.list_error: Exception | None = None
        self.save_error: Exception | None = None
        self.save_created = True
        self.saves: list[dict] = []
        self.delete_error: Exception | None = None
        self.deletes: list[dict] = []

    async def list_tickers(self):
        return self.tickers

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        return []

    async def list_holdings(self, *, user_id):
        return self.holdings

    async def backtest(self, *, assets, period, benchmark):
        raise PortfolioUnavailable("not under test")

    async def list_saved_labs(self, *, user_id):
        if self.list_error:
            raise self.list_error
        return self.saved_labs

    async def save_lab(self, *, user_id, name, assets, replace):
        self.saves.append({"user_id": user_id, "name": name, "assets": assets, "replace": replace})
        if self.save_error:
            raise self.save_error
        return SavedLabSaveResult(
            name=name.strip(), created=self.save_created, assets_saved=len(assets)
        )

    async def delete_saved_lab(self, *, user_id, name):
        self.deletes.append({"user_id": user_id, "name": name})
        if self.delete_error:
            raise self.delete_error


def _settings() -> Settings:
    return Settings(
        auth_api_url="http://auth.internal",
        internal_secret="test-secret",  # pragma: allowlist secret
        portfolio_api_url="http://portfolio.internal",
        portfolio_internal_secret="test-portfolio-secret",  # pragma: allowlist secret
        brand_dir=settings_module._DEFAULT_BRAND_DIR,
        cookie_secure=False,
        api_host="127.0.0.1",
        api_port=8000,
        http_timeout_seconds=5.0,
    )


@pytest.fixture
def client():
    fake_auth, fake_portfolio = FakeAuthClient(), FakePortfolioClient()
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = lambda: fake_portfolio
    with TestClient(app) as test_client:
        user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
        fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
        test_client.cookies.set("stakehold_session", "tok")
        test_client.cookies.set("stakehold_csrf", CSRF_TOKEN)
        yield test_client, fake_portfolio


def _holding(ticker, value):
    return Holding(
        canonical_ticker=ticker,
        quantity=Decimal("1"),
        average_cost_eur=Decimal("1"),
        cost_basis_eur=Decimal("1"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=Decimal("1"),
        market_value_native=Decimal(value),
        priced=True,
        price_date=None,
        market_value_eur=Decimal(value),
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


def _text(html):
    return " ".join(unescape(re.sub(r"<[^>]+>", "", html)).split())


def _page(test_client, query=""):
    response = test_client.get("/labs" + query)
    assert response.status_code == 200
    return response.text


def _form(html, action):
    match = re.search(
        rf'<form method="post" action="{re.escape(action)}"[^>]*>(.*?)</form>', html, re.S
    )
    return None if match is None else match.group(1)


def _hidden(form_html):
    return re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form_html)


def _query(location):
    parts = urlsplit(location)
    return parts.path, parse_qsl(parts.query, keep_blank_values=True)


MIX = [("ticker", "IWDA.AS"), ("weight", "60"), ("ticker", "AAPL"), ("weight", "40")]


def _save(test_client, name="Core", *, mix=MIX, csrf_token=CSRF_TOKEN, **extra):
    data = [("csrf_token", csrf_token), ("name", name), *mix, *extra.items()]
    # Encoded by hand: httpx sends a list of pairs as raw content, and the mix repeats its keys.
    return test_client.post(
        "/labs/saved",
        content=urlencode(data),
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


# --- the list on /labs ------------------------------------------------------------------------


def test_the_saved_labs_fold_lists_each_lab_with_an_open_link(client):
    test_client, fake = client
    fake.saved_labs = [CORE, TECH]

    html = _page(test_client, "?ticker=AAPL&weight=100")

    assert "Saved labs" in html
    fold = html.split('id="saved-labs"', 1)[1]
    assert '<span class="mono">2</span> labs' in fold
    links = re.findall(r'<a class="saved-lab-name" href="([^"]+)">([^<]+)</a>', fold)
    assert [name for _, name in links] == ["Core", "Tech"]
    path, params = _query(links[0][0].replace("&amp;", "&"))
    assert path == "/labs"
    assert params == MIX


def test_each_saved_lab_has_a_delete_link_back_to_this_view(client):
    test_client, fake = client
    fake.saved_labs = [CORE]

    html = _page(test_client, "?ticker=AAPL&weight=100&period=5Y")

    href = re.search(r'<a class="btn btn-ghost" href="(/labs/saved/delete[^"]+)"', html)[1]
    path, params = _query(href.replace("&amp;", "&"))
    assert path == "/labs/saved/delete"
    assert dict(params)["name"] == "Core"
    assert dict(params)["back"] == "ticker=AAPL&weight=100&period=5Y"
    assert 'data-confirm="delete-lab-dialog"' in html
    assert 'data-confirm-summary="Core — IWDA.AS 60% · AAPL 40%"' in html


def test_the_lab_the_page_shows_is_marked_and_named_in_the_save_field(client):
    test_client, fake = client
    fake.saved_labs = [CORE, TECH]

    html = _page(test_client, "?ticker=AAPL&weight=100")

    assert re.search(r'<li class="saved-lab" aria-current="true">\s*<a [^>]+>Tech</a>', html)
    assert re.search(r'<input [^>]*id="lab-name"[^>]*value="Tech"', html)


def test_no_saved_labs_reads_as_such(client):
    test_client, _ = client

    html = _page(test_client, "?ticker=AAPL&weight=100")

    assert "No saved labs yet." in _text(html)


def test_a_failed_list_shows_a_notice_and_keeps_the_save_form(client):
    test_client, fake = client
    fake.list_error = PortfolioUnavailable("down")

    html = _page(test_client, "?ticker=AAPL&weight=100")

    assert "Can't load your saved labs right now." in _text(html)
    assert "No saved labs yet." not in _text(html)
    assert _form(html, "/labs/saved") is not None


# --- the save form on /labs -------------------------------------------------------------------


def test_the_save_form_carries_the_shown_mix_and_the_view(client):
    test_client, _ = client

    html = _page(test_client, "?ticker=aapl&weight=100&period=5Y")

    form = _form(html, "/labs/saved")
    hidden = _hidden(form)
    assert ("csrf_token", CSRF_TOKEN) in hidden
    # In the tracked spelling, as the table shows it.
    assert ("ticker", "AAPL") in hidden and ("weight", "100") in hidden
    assert ("period", "5Y") in hidden
    assert re.search(r'<input [^>]*name="name"[^>]*maxlength="100"[^>]*required', form)


def test_the_default_mix_can_be_saved(client):
    test_client, fake = client
    fake.holdings = Holdings(holdings=(_holding("IWDA.AS", "750"),), totals=None)

    html = _page(test_client)

    assert ("ticker", "IWDA.AS") in _hidden(_form(html, "/labs/saved"))


def test_a_mix_with_errors_offers_no_save(client):
    test_client, _ = client

    html = _page(test_client, "?ticker=NOPE&weight=100")

    assert _form(html, "/labs/saved") is None


def test_an_empty_mix_offers_no_save(client):
    test_client, _ = client

    assert _form(_page(test_client, "?ticker="), "/labs/saved") is None


# --- status banners ---------------------------------------------------------------------------


def test_after_a_save_the_banner_names_the_lab_from_portfolio(client):
    test_client, fake = client
    fake.saved_labs = [TECH]

    html = _page(test_client, "?ticker=AAPL&weight=100&saved=1")

    assert "Saved. This mix is your lab Tech." in _text(html)


@pytest.mark.parametrize(
    "flag, message",
    [
        ("lab_deleted=1", "Lab deleted."),
        ("lab_deleted=0", "Nothing deleted. That lab no longer exists."),
    ],
)
def test_after_a_delete_the_banner_says_what_happened(client, flag, message):
    test_client, _ = client

    assert message in _text(_page(test_client, f"?ticker=AAPL&weight=100&{flag}"))


def test_status_flags_stay_out_of_every_link_and_form(client):
    test_client, fake = client
    fake.saved_labs = [CORE]

    html = _page(test_client, "?ticker=AAPL&weight=100&saved=1")

    assert "saved=1" not in html.split("</h1>", 1)[1].replace("Saved.", "")
    assert 'name="saved"' not in html


# --- POST /labs/saved -------------------------------------------------------------------------


def test_save_sends_the_mix_and_redirects_to_it_with_the_view(client):
    test_client, fake = client

    response = _save(test_client, " Core ", period="5Y", benchmark="IWDA.AS")

    assert response.status_code == 303
    path, params = _query(response.headers["location"])
    assert path == "/labs"
    assert params == [*MIX, ("period", "5Y"), ("benchmark", "IWDA.AS"), ("saved", "1")]
    assert fake.saves == [
        {
            "user_id": "u1",
            "name": " Core ",
            "assets": [("IWDA.AS", Decimal("60")), ("AAPL", Decimal("40"))],
            "replace": False,
        }
    ]


def test_save_reads_weights_as_the_mix_form_does(client):
    test_client, fake = client

    _save(test_client, mix=[("ticker", "AAPL"), ("weight", "12,5%")])

    assert fake.saves[0]["assets"] == [("AAPL", Decimal("12.5"))]


def test_a_taken_name_asks_before_replacing(client):
    test_client, fake = client
    fake.save_error = SavedLabNameTaken("You already have a lab called 'Core'.")

    response = _save(test_client, "Core", period="5Y")

    # A question, not an error: like the delete's confirmation page.
    assert response.status_code == 200
    text = _text(response.text)
    assert "You already have a lab called Core." in text
    form = _form(response.text, "/labs/saved")
    hidden = _hidden(form)
    assert ("replace", "1") in hidden and ("name", "Core") in hidden
    assert ("ticker", "IWDA.AS") in hidden and ("period", "5Y") in hidden
    assert "Replace it" in form
    back = re.search(r'<a class="btn btn-ghost" href="([^"]+)">Back to Labs</a>', response.text)
    assert back[1].replace("&amp;", "&") == "/labs?" + "&".join(
        f"{k}={v}" for k, v in [*MIX, ("period", "5Y")]
    )


def test_replace_sends_replace_and_redirects(client):
    test_client, fake = client
    fake.save_created = False

    response = _save(test_client, "Core", replace="1")

    assert response.status_code == 303
    assert fake.saves[0]["replace"] is True


def test_a_mix_with_errors_is_refused_without_calling_portfolio(client):
    test_client, fake = client

    response = _save(test_client, mix=[("ticker", "AAPL"), ("weight", "lots")])

    assert response.status_code == 422
    assert "Fix the marked rows in the mix before saving it." in _text(response.text)
    assert fake.saves == []


def test_an_empty_mix_is_refused_without_calling_portfolio(client):
    test_client, fake = client

    response = _save(test_client, mix=[("ticker", "")])

    assert response.status_code == 422
    assert "Add an asset to the mix before saving it." in _text(response.text)
    assert fake.saves == []


def test_portfolios_refusal_is_shown_as_is_under_the_name(client):
    test_client, fake = client
    fake.save_error = InvalidPortfolioRequest("Enter a name for the lab.")

    response = _save(test_client, "  ")

    assert response.status_code == 422
    assert "Couldn't save the lab. Enter a name for the lab." in _text(response.text)
    assert re.search(r'<input [^>]*id="lab-name"[^>]*aria-invalid="true"', response.text)


def test_an_outage_says_so_and_keeps_the_form(client):
    test_client, fake = client
    fake.save_error = PortfolioUnavailable("down")

    response = _save(test_client)

    assert response.status_code == 503
    assert "Can't reach the portfolio service right now" in _text(response.text)
    assert _form(response.text, "/labs/saved") is not None


def test_save_rejects_a_bad_csrf_token(client):
    test_client, fake = client

    response = _save(test_client, csrf_token="forged")

    assert response.status_code == 400
    assert "Refresh the page and try again." in _text(response.text)
    assert fake.saves == []


# --- delete -----------------------------------------------------------------------------------


def _delete(test_client, name="Core", back="ticker=AAPL&weight=100", csrf_token=CSRF_TOKEN):
    return test_client.post(
        "/labs/saved/delete",
        data={"csrf_token": csrf_token, "name": name, "back": back},
        follow_redirects=False,
    )


def test_the_confirmation_page_names_the_lab_and_its_mix(client):
    test_client, fake = client
    fake.saved_labs = [CORE]

    response = test_client.get("/labs/saved/delete", params={"name": "Core", "back": "period=5Y"})

    assert response.status_code == 200
    text = _text(response.text)
    assert "Delete this lab?" in text
    assert "Core — IWDA.AS 60% · AAPL 40%" in text
    hidden = dict(_hidden(_form(response.text, "/labs/saved/delete")))
    assert hidden == {"csrf_token": CSRF_TOKEN, "name": "Core", "back": "period=5Y"}
    assert 'href="/labs?period=5Y"' in response.text


def test_the_confirmation_page_for_a_gone_lab_is_a_404(client):
    test_client, _ = client

    response = test_client.get("/labs/saved/delete", params={"name": "Core"})

    assert response.status_code == 404
    assert "You have no lab called Core." in _text(response.text)


def test_the_confirmation_page_still_confirms_when_the_list_is_down(client):
    test_client, fake = client
    fake.list_error = PortfolioUnavailable("down")

    response = test_client.get("/labs/saved/delete", params={"name": "Core"})

    assert response.status_code == 200
    assert _form(response.text, "/labs/saved/delete") is not None


def test_delete_redirects_back_to_the_view_it_came_from(client):
    test_client, fake = client

    response = _delete(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/labs?ticker=AAPL&weight=100&lab_deleted=1"
    assert fake.deletes == [{"user_id": "u1", "name": "Core"}]


def test_delete_of_a_gone_lab_says_nothing_was_deleted(client):
    test_client, fake = client
    fake.delete_error = SavedLabNotFound("You have no lab called 'Core'.")

    response = _delete(test_client, back="")

    assert response.headers["location"] == "/labs?lab_deleted=0"


def test_delete_back_is_only_ever_a_labs_query(client):
    test_client, _ = client

    response = _delete(test_client, back="//evil.example/x?a=1#f")

    location = response.headers["location"]
    assert location.startswith("/labs?")
    assert "evil.example" not in urlsplit(location).netloc


def test_delete_outage_shows_the_confirmation_page_with_an_error(client):
    test_client, fake = client
    fake.delete_error = PortfolioUnavailable("down")

    response = _delete(test_client)

    assert response.status_code == 503
    assert "Couldn't delete." in _text(response.text)
    assert _form(response.text, "/labs/saved/delete") is not None


def test_delete_rejects_a_bad_csrf_token(client):
    test_client, fake = client

    response = _delete(test_client, csrf_token="forged")

    assert response.status_code == 400
    assert fake.deletes == []
