"""Editing a transaction: the Edit link on each row of the transaction log, the edit page it opens
(the add form's fields, prefilled), and the POST that saves the edit.

Like test_delete_pages.py, it carries its own minimal fakes: only the calls these pages make.
"""

import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import Account, Transaction, TransactionUpdateResult
from web.portfolio_client.errors import (
    InvalidPortfolioRequest,
    PortfolioUnavailable,
    TransactionNotFound,
)
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
CSRF_TOKEN = "csrf-tok"


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.list_accounts_result: list[Account] = [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]
        self.list_transactions_result: list[Transaction] = [_buy()]
        self.list_transactions_error: Exception | None = None
        self.update_result = TransactionUpdateResult(
            status="ok", transactions_updated=1, positions_rebuild="ok", returns_rebuild="ok"
        )
        self.update_error: Exception | None = None
        self.updates: list[dict] = []

    async def list_accounts(self, *, user_id):
        return self.list_accounts_result

    async def list_transactions(self, *, user_id, account_label):
        if self.list_transactions_error:
            raise self.list_transactions_error
        return self.list_transactions_result

    async def list_tickers(self):
        return ["VWCE.DE", "IWDA.AS"]

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        return []

    async def update_transaction(self, **kwargs):
        self.updates.append(kwargs)
        if self.update_error:
            raise self.update_error
        return self.update_result


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
        yield test_client, fake_auth, fake_portfolio


def _logged_in(test_client, fake_auth):
    user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
    fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")
    test_client.cookies.set("stakehold_csrf", CSRF_TOKEN)


def _buy(**overrides) -> Transaction:
    values = dict(
        account_id=1,
        broker_transaction_ref="buy-1",
        transaction_type="BUY",
        trade_datetime=datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc),
        instrument_identifier="VWCE.DE",
        instrument_identifier_kind="TICKER",
        quantity=Decimal("10.000000"),
        price=Decimal("112.3400"),
        native_currency="EUR",
        native_amount=Decimal("-1124.4000"),
        eur_amount=Decimal("-1124.4000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=Decimal("1.0000"),
    )
    values.update(overrides)
    return Transaction(**values)


def _deposit() -> Transaction:
    return _buy(
        broker_transaction_ref="dep-1",
        transaction_type="DEPOSIT",
        instrument_identifier=None,
        instrument_identifier_kind=None,
        quantity=None,
        price=None,
        native_currency="USD",
        native_amount=Decimal("500.0000"),
        eur_amount=Decimal("462.9630"),
        fx_rate=Decimal("1.080000"),
        fx_rate_source="broker_reported",
        fees_native=None,
    )


def _edit_page(test_client, account="main", ref="buy-1"):
    return test_client.get(
        "/transactions/edit", params={"account": account, "ref": ref}, follow_redirects=False
    )


def _form_data(**overrides):
    data = {
        "account": "main",
        "ref": "buy-1",
        "transaction_type": "BUY",
        "trade_datetime": "2026-01-05T09:30",
        "native_amount": "",
        "native_currency": "EUR",
        "instrument_identifier": "VWCE.DE",
        "original_instrument_identifier": "VWCE.DE",
        "original_instrument_identifier_kind": "TICKER",
        "quantity": "12",
        "price": "112.34",
        "fx_rate": "",
        "fees_native": "1",
        "csrf_token": CSRF_TOKEN,
    }
    data.update(overrides)
    return data


def _post(test_client, **overrides):
    return test_client.post(
        "/transactions/edit", data=_form_data(**overrides), follow_redirects=False
    )


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _form(html: str) -> str:
    match = re.search(r'<form\b[^>]*action="/transactions/edit"[^>]*>.*?</form>', html, re.S)
    assert match, "no form posting to /transactions/edit"
    return match.group(0)


def _input_value(form: str, name: str) -> str | None:
    match = re.search(rf'<input\b[^>]*\bname="{name}"[^>]*>', form)
    if match is None:
        return None
    value = re.search(r'\bvalue="([^"]*)"', match.group(0))
    return value.group(1) if value else None


def _selected_type(form: str) -> str | None:
    match = re.search(r'<option value="([A-Z]+)" selected>', form)
    return match.group(1) if match else None


# --- the edit page ----------------------------------------------------------------


def test_edit_page_requires_login(client):
    test_client, _, _ = client

    response = _edit_page(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_edit_page_prefills_a_buy_and_leaves_its_amount_blank(client):
    """The amount of a BUY/SELL is left for portfolio to derive: prefilled, it would no longer
    match price x quantity + fees once either changed, and the edit would be refused."""
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _edit_page(test_client)

    assert response.status_code == 200
    assert "Edit transaction" in response.text
    form = _form(response.text)
    assert _selected_type(form) == "BUY"
    assert _input_value(form, "trade_datetime") == "2026-01-05T09:30"
    assert _input_value(form, "native_amount") == ""
    assert _input_value(form, "native_currency") == "EUR"
    assert _input_value(form, "instrument_identifier") == "VWCE.DE"
    assert _input_value(form, "quantity") == "10"
    assert _input_value(form, "price") == "112.34"
    assert _input_value(form, "fees_native") == "1"
    assert _input_value(form, "csrf_token") == CSRF_TOKEN
    assert _input_value(form, "account") == "main"


def test_edit_page_shows_the_reference_but_does_not_let_it_change(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    form = _form(_edit_page(test_client).text)

    assert re.search(r'<input type="text" id="broker_transaction_ref"[^>]*readonly', form)
    assert _input_value(form, "ref") == "buy-1"


def test_edit_page_prefills_a_cash_rows_amount_and_rate(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [_deposit()]

    form = _form(_edit_page(test_client, ref="dep-1").text)

    assert _selected_type(form) == "DEPOSIT"
    assert _input_value(form, "native_amount") == "500"
    assert _input_value(form, "native_currency") == "USD"
    assert _input_value(form, "fx_rate") == "1.08"
    assert _input_value(form, "instrument_identifier") == ""


def test_edit_page_carries_an_imported_instruments_kind_and_says_it_is_kept(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [
        _buy(instrument_identifier="IE00B4L5Y983", instrument_identifier_kind="ISIN")
    ]

    html = _edit_page(test_client).text

    form = _form(html)
    assert _input_value(form, "original_instrument_identifier") == "IE00B4L5Y983"
    assert _input_value(form, "original_instrument_identifier_kind") == "ISIN"
    assert "Kept as your broker recorded it (ISIN)" in _text(html)


def test_edit_page_cancel_goes_back_to_the_log(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _edit_page(test_client).text

    assert re.search(r'<a [^>]*href="/transactions\?account=main"[^>]*>Cancel</a>', html)


def test_edit_page_unknown_account_renders_404(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _edit_page(test_client, account="someone-elses")

    assert response.status_code == 404
    assert "Account not found" in response.text


def test_edit_page_for_a_transaction_already_gone_goes_back_to_the_log(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _edit_page(test_client, ref="gone")

    assert response.status_code == 303
    assert response.headers["location"] == "/transactions?account=main&edited=gone"


def test_edit_page_degrades_when_transactions_unavailable(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_error = PortfolioUnavailable("down")

    response = _edit_page(test_client)

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service right now" in response.text
    assert 'action="/transactions/edit"' not in response.text


# --- saving the edit ----------------------------------------------------------------


def test_save_sends_the_edit_and_goes_back_to_the_log_with_the_outcome(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = _post(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/transactions?account=main&edited=1&positions=ok&returns=ok"
    )
    assert fake_portfolio.updates == [
        {
            "user_id": "u1",
            "account_label": "main",
            "broker_transaction_ref": "buy-1",
            "transaction_type": "BUY",
            "trade_datetime": datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc),
            "native_amount": None,
            "native_currency": "EUR",
            "instrument_identifier": "VWCE.DE",
            "instrument_identifier_kind": "TICKER",
            "quantity": Decimal("12"),
            "price": Decimal("112.34"),
            "fx_rate": None,
            "fees_native": Decimal("1"),
        }
    ]


def test_save_keeps_an_unchanged_instruments_kind(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    _post(
        test_client,
        instrument_identifier=" IE00B4L5Y983 ",
        original_instrument_identifier="IE00B4L5Y983",
        original_instrument_identifier_kind="ISIN",
    )

    [update] = fake_portfolio.updates
    assert (update["instrument_identifier"], update["instrument_identifier_kind"]) == (
        "IE00B4L5Y983",
        "ISIN",
    )


def test_save_sends_a_changed_instrument_as_a_ticker(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    _post(
        test_client,
        instrument_identifier="IWDA.AS",
        original_instrument_identifier="IE00B4L5Y983",
        original_instrument_identifier_kind="ISIN",
    )

    [update] = fake_portfolio.updates
    assert update["instrument_identifier_kind"] == "TICKER"


def test_save_sends_no_instrument_for_a_blank_one(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    _post(test_client, transaction_type="DEPOSIT", instrument_identifier="", native_amount="5")

    [update] = fake_portfolio.updates
    assert (update["instrument_identifier"], update["instrument_identifier_kind"]) == (None, None)


def test_save_that_changed_nothing_says_so(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.update_result = TransactionUpdateResult(
        status="nothing_to_do",
        transactions_updated=0,
        positions_rebuild="skipped",
        returns_rebuild="skipped",
    )

    response = _post(test_client)

    assert response.headers["location"] == "/transactions?account=main&edited=0"


def test_save_of_a_transaction_gone_meanwhile_goes_back_to_the_log(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.update_error = TransactionNotFound("no transaction")

    response = _post(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/transactions?account=main&edited=gone"


def test_save_refused_by_portfolio_shows_its_message_and_keeps_what_was_typed(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.update_error = InvalidPortfolioRequest("BUY requires a positive quantity")

    response = _post(test_client, quantity="-3")

    assert response.status_code == 422
    assert "BUY requires a positive quantity" in response.text
    assert "Couldn&#39;t save." in response.text
    form = _form(response.text)
    assert _input_value(form, "quantity") == "-3"
    assert _input_value(form, "ref") == "buy-1"
    assert _input_value(form, "original_instrument_identifier_kind") == "TICKER"


def test_save_refused_by_portfolio_over_htmx_swaps_only_the_error(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.update_error = InvalidPortfolioRequest("BUY requires a positive quantity")

    response = test_client.post(
        "/transactions/edit", data=_form_data(), headers={"HX-Request": "true"}
    )

    assert response.status_code == 422
    assert response.headers["X-Form-Error"] == "true"
    assert "BUY requires a positive quantity" in response.text
    assert "<form" not in response.text


def test_save_with_an_unreadable_number_is_refused_before_portfolio(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = _post(test_client, quantity="twelve")

    assert response.status_code == 400
    assert "Enter a valid date/time and numeric fields." in response.text
    assert fake_portfolio.updates == []


def test_save_with_a_bad_csrf_token_is_refused(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = _post(test_client, csrf_token="wrong")

    assert response.status_code == 400
    assert fake_portfolio.updates == []


def test_save_when_portfolio_is_down_says_so(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.update_error = PortfolioUnavailable("down")

    response = _post(test_client)

    assert response.status_code == 503
    assert "Can&#39;t reach the portfolio service right now." in response.text


# --- the log: Edit links and the outcome ------------------------------------------------


def _log(test_client, query=""):
    return test_client.get(f"/transactions?account=main{query}")


def test_log_links_each_row_to_its_edit_page(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [_buy(broker_transaction_ref="buy 1/a")]

    html = _log(test_client).text

    assert re.search(
        r'<a class="btn btn-ghost" href="/transactions/edit\?account=main&amp;ref=buy%201/a">'
        r'Edit<span class="visually-hidden"> BUY on 2026-01-05</span></a>',
        html,
    )


def test_log_says_an_edit_was_saved_with_the_rebuild_outcome(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    text = _text(_log(test_client, "&edited=1&positions=ok&returns=ok").text)

    assert "Transaction updated. Holdings and returns updated." in text


def test_log_says_an_edit_changed_nothing(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    text = _text(_log(test_client, "&edited=0").text)

    assert "Nothing changed. The transaction already read that way." in text


def test_log_says_an_edited_transaction_was_gone(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    text = _text(_log(test_client, "&edited=gone").text).replace("&#39;", "'")

    assert "Nothing saved. That transaction isn't in this account any more." in text


def test_log_ignores_an_unknown_edit_outcome(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    text = _text(_log(test_client, "&edited=<b>hi</b>").text)

    assert "Transaction updated" not in text
    assert "Nothing changed" not in text
    assert "Nothing saved" not in text
