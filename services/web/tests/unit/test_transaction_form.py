"""GET /transactions/new's form shape (UI-9): fieldsets, prefills, and the per-type field mapping
static/transaction_form.js reads.

Kept apart from test_routes.py, which is at the repo's 64KB file limit; the submit tests stay
there unchanged. Like test_transactions_page.py, it carries its own minimal fakes: only the calls
this one page makes.

There's no JS test runner, so what's tested here is the contract the script relies on: which
types each field is marked for, and that the server-rendered form hides and disables nothing,
so it still posts every field without JS.
"""

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api import routes
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import Account
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
ALL_TYPES = {"BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FX", "FEE", "ADJUSTMENT"}
TRADES = {"BUY", "SELL"}

# Every form field transaction_new_submit reads.
POSTED_FIELDS = {
    "csrf_token",
    "account_label",
    "broker_transaction_ref",
    "transaction_type",
    "trade_datetime",
    "native_amount",
    "native_currency",
    "instrument_identifier",
    "quantity",
    "price",
    "fx_rate",
    "fees_native",
}


class FakeAuthClient:
    """Anonymous until `_logged_in` sets a result."""

    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    async def list_accounts(self, *, user_id):
        return [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]

    async def list_tickers(self):
        return ["IWDA.AS"]


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
def client(monkeypatch):
    monkeypatch.setattr(
        routes, "_utcnow", lambda: datetime(2026, 10, 9, 14, 5, 42, tzinfo=timezone.utc)
    )
    fake_auth = FakeAuthClient()
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = lambda: FakePortfolioClient()
    with TestClient(app) as test_client:
        user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
        fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
        test_client.cookies.set("stakehold_session", "tok")
        yield test_client


class _FormParser(HTMLParser):
    """Collects the form's controls, its `.field` wrappers (with the control each one holds),
    any other element carrying data-types, and the fieldset legends."""

    def __init__(self):
        super().__init__()
        self.controls: list[dict] = []
        self.fields: list[dict] = []
        self.marked: list[dict] = []
        self.legends: list[str] = []
        self._open_fields: list[dict] = []
        self._in_legend = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "div" and "field" in classes:
            field = {"attrs": attrs, "controls": []}
            self.fields.append(field)
            self._open_fields.append(field)
        elif tag == "div":
            self._open_fields.append(None)
        elif "data-types" in attrs:
            self.marked.append({"tag": tag, "attrs": attrs})
        if tag in ("input", "select", "textarea") and attrs.get("name"):
            control = {"tag": tag, "attrs": attrs}
            self.controls.append(control)
            open_field = next((f for f in reversed(self._open_fields) if f), None)
            if open_field is not None:
                open_field["controls"].append(control)
        if tag == "legend":
            self._in_legend = True

    def handle_endtag(self, tag):
        if tag == "div" and self._open_fields:
            self._open_fields.pop()
        if tag == "legend":
            self._in_legend = False

    def handle_data(self, data):
        if self._in_legend and data.strip():
            self.legends.append(data.strip())


def _form(test_client) -> _FormParser:
    response = test_client.get("/transactions/new", params={"account": "main"})
    assert response.status_code == 200
    # The header's theme toggle and account switcher are forms too.
    form_html = response.text.split('<form class="transaction-form"', 1)[1].split("</form>", 1)[0]
    parser = _FormParser()
    parser.feed("<form" + form_html)
    return parser


def _field(parser: _FormParser, name: str) -> dict:
    return next(f for f in parser.fields if any(c["attrs"]["name"] == name for c in f["controls"]))


def _types(element: dict, attribute: str = "data-types") -> set[str] | None:
    value = element["attrs"].get(attribute)
    return None if value is None else set(value.split())


def _control_value(parser: _FormParser, name: str) -> str | None:
    return next(c["attrs"].get("value") for c in parser.controls if c["attrs"]["name"] == name)


# --- prefills -------------------------------------------------------------------------------


def test_reference_is_prefilled_with_a_manual_uuid4(client):
    reference = _control_value(_form(client), "broker_transaction_ref")

    assert re.fullmatch(
        r"manual-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", reference
    )


def test_reference_is_unique_per_render(client):
    first = _control_value(_form(client), "broker_transaction_ref")
    second = _control_value(_form(client), "broker_transaction_ref")

    assert first != second


def test_reference_stays_editable_and_required(client):
    control = next(
        c for c in _form(client).controls if c["attrs"]["name"] == "broker_transaction_ref"
    )

    assert "readonly" not in control["attrs"]
    assert "required" in control["attrs"]


def test_date_defaults_to_now_in_utc_to_the_minute(client):
    assert _control_value(_form(client), "trade_datetime") == "2026-10-09T14:05"


def test_currency_defaults_to_eur_and_suggests_the_supported_currencies(client):
    response = client.get("/transactions/new", params={"account": "main"})
    parser = _form(client)

    assert _control_value(parser, "native_currency") == "EUR"
    currency = next(c for c in parser.controls if c["attrs"]["name"] == "native_currency")
    assert currency["attrs"]["list"] == "currency-options"
    options = response.text.split('<datalist id="currency-options">', 1)[1].split("</datalist>")[0]
    assert re.findall(r'<option value="([A-Z]{3})">', options) == [
        "EUR",
        "USD",
        "GBP",
        "CHF",
        "PLN",
    ]


# --- no-JS guarantee ------------------------------------------------------------------------


def test_every_posted_field_is_rendered_enabled_and_visible(client):
    parser = _form(client)

    names = {c["attrs"]["name"] for c in parser.controls}
    assert POSTED_FIELDS <= names
    for control in parser.controls:
        assert "disabled" not in control["attrs"], control
        assert "hidden" not in control["attrs"], control
    for field in parser.fields:
        assert "hidden" not in field["attrs"], field


def test_fields_required_only_for_some_types_are_not_required_in_the_html(client):
    """`required` is the script's to toggle per type: set in the HTML, a no-JS deposit couldn't
    be submitted without a ticker. portfolio still rejects whatever's missing."""
    parser = _form(client)

    for name in ("native_amount", "instrument_identifier", "quantity", "price", "fx_rate"):
        control = next(c for c in parser.controls if c["attrs"]["name"] == name)
        assert "required" not in control["attrs"], name


def test_fields_are_grouped_into_the_four_fieldsets(client):
    assert _form(client).legends == ["What", "When", "Amount", "Instrument"]


def test_form_loads_its_script_from_static(client):
    response = client.get("/transactions/new", params={"account": "main"})

    assert re.search(
        r'<script src="/static/transaction_form\.js\?v=\w+" defer></script>', response.text
    )


# --- the type -> fields mapping (confirmed against portfolio's validate()/_manual_amount) ----


def test_always_shown_fields_carry_no_type_marker(client):
    parser = _form(client)

    for name in ("transaction_type", "broker_transaction_ref", "trade_datetime", "native_currency"):
        assert _types(_field(parser, name)) is None, name


def test_amount_is_hidden_for_trades_and_required_for_everything_else(client):
    field = _field(_form(client), "native_amount")

    assert _types(field) == ALL_TYPES - TRADES
    assert _types(field, "data-required-for") == ALL_TYPES - TRADES


def test_trade_fields_are_shown_and_required_for_buy_and_sell(client):
    parser = _form(client)

    for name in ("quantity", "price"):
        field = _field(parser, name)
        assert _types(field) == TRADES | {"ADJUSTMENT"}, name
        assert _types(field, "data-required-for") == TRADES, name


def test_instrument_is_also_offered_for_a_dividend(client):
    field = _field(_form(client), "instrument_identifier")

    assert _types(field) == TRADES | {"DIVIDEND", "ADJUSTMENT"}
    assert _types(field, "data-required-for") == TRADES


def test_fees_are_shown_for_trades_only(client):
    field = _field(_form(client), "fees_native")

    assert _types(field) == TRADES | {"ADJUSTMENT"}
    assert _types(field, "data-required-for") is None


def test_adjustment_shows_every_type_dependent_field(client):
    parser = _form(client)

    for field in parser.fields:
        types = _types(field)
        if types is not None:
            assert "ADJUSTMENT" in types, field


def test_derived_amount_note_shows_for_trades(client):
    notes = _form(client).marked

    assert [_types(note) for note in notes] == [TRADES]


def test_fx_rate_is_hidden_for_eur_and_shown_for_every_type(client):
    field = _field(_form(client), "fx_rate")

    assert field["attrs"].get("data-hide-for-currency") == "EUR"
    assert _types(field) is None


def test_the_ticker_field_links_to_adding_a_ticker(client):
    # Ticker onboarding: the picker lists what Stakehold already prices; anything else is added
    # from the Tickers page, not typed in here.
    response = client.get("/transactions/new", params={"account": "main"})

    assert 'href="/tickers/new"' in response.text
    assert "Not in the list?" in response.text
