"""GET /portfolio: every holding summed across the user's accounts (PF-4) -- the tiles from
portfolio's totals, the holdings table with its per-account breakdown, holdings still waiting
for prices, instruments waiting for a mapping, and the empty and error states. The old
/portfolio?account=X redirect is tested with the page it points at, in
test_account_holdings_page.py.

Like test_home_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    Account,
    Holding,
    HoldingAccount,
    Holdings,
    HoldingsTotals,
    UnmappedInstrument,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"


class FakeAuthClient:
    """Anonymous until `_logged_in` sets a result."""

    def __init__(self):
        self.validate_result: AuthResult | None = None
        self.calls = 0

    async def validate_session(self, *, cookie_header):
        self.calls += 1
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.list_accounts_result: list[Account] = [
            Account(id=1, account_label="A", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]
        self.list_accounts_error: Exception | None = None
        self.holdings = Holdings(holdings=(), totals=None)
        self.list_holdings_error: Exception | None = None
        self.unmapped: list[UnmappedInstrument] = []
        self.list_unmapped_instruments_error: Exception | None = None
        # {ticker: status} data-store reports for tickers it tracks directly (TK-9).
        self.tracked_statuses: dict[str, str] = {}
        self.list_tracked_tickers_error: Exception | None = None
        self.calls: list[tuple] = []

    async def list_accounts(self, *, user_id):
        self.calls.append(("list_accounts", user_id))
        if self.list_accounts_error:
            raise self.list_accounts_error
        return self.list_accounts_result

    async def list_holdings(self, *, user_id):
        self.calls.append(("list_holdings", user_id))
        if self.list_holdings_error:
            raise self.list_holdings_error
        return self.holdings

    async def list_unmapped_instruments(self, *, user_id):
        self.calls.append(("list_unmapped_instruments", user_id))
        if self.list_unmapped_instruments_error:
            raise self.list_unmapped_instruments_error
        return self.unmapped

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.calls.append(("list_tracked_tickers", tuple(tickers or ())))
        if self.list_tracked_tickers_error:
            raise self.list_tracked_tickers_error
        return [
            SimpleNamespace(ticker=ticker, status=self.tracked_statuses[ticker])
            for ticker in tickers or ()
            if ticker in self.tracked_statuses
        ]


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


def _breakdown(label, *, quantity="10", currency="EUR", cost="1000", value="1200"):
    priced = value is not None
    return HoldingAccount(
        account_label=label,
        quantity=Decimal(quantity),
        native_currency=currency,
        average_cost_native=Decimal(cost) / Decimal(quantity),
        cost_basis_eur=Decimal(cost),
        market_value_native=Decimal(value) if priced else None,
        market_value_eur=Decimal(value) if priced else None,
        unrealized_return_eur=Decimal(value) - Decimal(cost) if priced else None,
    )


def _holding(
    ticker="IWDA.AS",
    *,
    accounts=None,
    currency="EUR",
    mixed=False,
    value="1200",
    cost="1000",
    dividends="0",
    average_cost="100",
):
    """A holding as portfolio's GET /holdings serves it; unpriced when `value` is None."""
    accounts = accounts or (_breakdown("A", currency=currency, cost=cost, value=value),)
    priced = value is not None
    unrealized = Decimal(value) - Decimal(cost) if priced else None
    total = unrealized + Decimal(dividends) if priced else None
    return Holding(
        canonical_ticker=ticker,
        quantity=sum((a.quantity for a in accounts), Decimal("0")),
        average_cost_eur=None if average_cost is None else Decimal(average_cost),
        cost_basis_eur=Decimal(cost),
        native_currency=None if mixed else currency,
        mixed_currency=mixed,
        average_cost_native=None if mixed else Decimal(average_cost or "0") * 2,
        market_value_native=None if mixed or not priced else Decimal(value) * 2,
        priced=priced,
        price_date=date(2026, 1, 6) if priced else None,
        market_value_eur=Decimal(value) if priced else None,
        unrealized_return_eur=unrealized,
        unrealized_return_pct=unrealized / Decimal(cost) if priced else None,
        dividends_received_eur=Decimal(dividends) if priced else None,
        total_return_eur=total,
        total_return_pct=total / Decimal(cost) if priced else None,
        accounts=tuple(accounts),
    )


def _totals(**changes):
    values = dict(
        price_date=date(2026, 1, 6),
        market_value_eur=Decimal("1200"),
        cost_basis_eur=Decimal("1000"),
        dividends_received_eur=Decimal("0"),
        total_return_eur=Decimal("200"),
        total_return_pct=Decimal("0.2"),
    )
    return HoldingsTotals(**{**values, **changes})


def _tile(html, label):
    """The value markup of the summary tile labelled `label`, or None if there's no such tile."""
    match = re.search(
        rf'<p class="tile-label">{re.escape(label)}</p>\s*<p class="tile-value">(.*?)</p>',
        html,
        re.S,
    )
    return None if match is None else match.group(1)


def _holding_row(html, ticker):
    """The holdings table's main row for `ticker` (not its breakdown row)."""
    match = re.search(
        rf'<tr role="row" class="holding-row">\s*<td[^>]*><span class="mono">'
        rf"{re.escape(ticker)}</span></td>.*?</tr>",
        html,
        re.S,
    )
    assert match, f"no holding row for {ticker}"
    return match.group(0)


def _breakdown_of(html, ticker):
    """The <details> breakdown that follows `ticker`'s main row."""
    row = _holding_row(html, ticker)
    after = html[html.index(row) + len(row) :]
    match = re.search(r"<details\b.*?</details>", after, re.S)
    assert match, f"no breakdown for {ticker}"
    return match.group(0)


def _cell(row_html, label):
    match = re.search(rf'<td[^>]*data-label="{re.escape(label)}"[^>]*>(.*?)</td>', row_html, re.S)
    return None if match is None else match.group(1)


def _page(test_client):
    response = test_client.get("/portfolio")
    assert response.status_code == 200
    return response.text


# --- access and nav ---------------------------------------------------------------------------


def test_anonymous_visitor_is_sent_to_login(client):
    test_client, _, _ = client

    response = test_client.get("/portfolio", follow_redirects=False)

    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith("/login")


def test_nav_has_a_portfolio_link_marked_current(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    nav = re.search(r'<nav class="site-nav".*?</nav>', html, re.S).group(0)
    labels = re.findall(r">(\w+)</a>", nav)
    assert labels[:3] == ["Overview", "Portfolio", "Accounts"]
    assert '<a href="/portfolio" aria-current="page">Portfolio</a>' in nav


def test_other_pages_link_to_portfolio_without_marking_it_current(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = []

    html = test_client.get("/").text

    assert '<a href="/portfolio">Portfolio</a>' in html


def test_redirect_with_an_account_still_skips_the_login_check(client):
    test_client, fake_auth, _ = client

    response = test_client.get("/portfolio", params={"account": "A"}, follow_redirects=False)

    assert response.status_code == 308
    assert fake_auth.calls == 0


def test_an_empty_account_param_is_a_bad_request(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/portfolio?account=", follow_redirects=False)

    assert response.status_code == 422


# --- summary tiles ----------------------------------------------------------------------------


def test_tiles_come_from_portfolios_totals_not_the_rows(client):
    """A closed position's dividends count in totals but in no row, so summing the rows would
    disagree with the home page."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(_holding(),),
        totals=_totals(dividends_received_eur=Decimal("50"), total_return_eur=Decimal("250")),
    )

    html = _page(test_client)

    assert "Prices as of" in html and "2026-01-06" in html
    assert "€1,200.00" in _tile(html, "Market value")
    assert "€1,000.00" in _tile(html, "Cost basis")
    assert "+€250.00" in _tile(html, "Total return")
    assert "+20.00%" in _tile(html, "Total return")
    assert "€50.00" in _tile(html, "Dividends received")


def test_no_tiles_while_no_account_has_prices(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding(value=None),), totals=None)

    html = _page(test_client)

    assert _tile(html, "Market value") is None
    assert "Prices as of" not in html


# --- the holdings table -----------------------------------------------------------------------


def test_priced_row_shows_its_figures_and_weight(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(
            _holding("IWDA.AS", value="3000", cost="2000", dividends="10"),
            _holding("VWCE.DE", value="1000", cost="1200"),
        ),
        totals=_totals(),
    )

    html = _page(test_client)

    row = _holding_row(html, "IWDA.AS")
    assert "10" in _cell(row, "Units")
    assert "€100.00" in _cell(row, "Avg cost")
    assert "€3,000.00" in _cell(row, "Market value")
    assert "75.00%" in _cell(row, "Weight")
    assert 'class="mono gain">+€1,000.00' in _cell(row, "Unrealized return")
    assert "+50.00%" in _cell(row, "Unrealized return (%)")
    assert "+€1,010.00" in _cell(row, "Total return")
    assert "+50.50%" in _cell(row, "Total return (%)")
    loss = _holding_row(html, "VWCE.DE")
    assert 'class="mono loss">−€200.00' in _cell(loss, "Unrealized return")
    assert "25.00%" in _cell(loss, "Weight")


def test_an_eur_holding_has_no_native_line(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding(currency="EUR"),), totals=_totals())

    row = _holding_row(_page(test_client), "IWDA.AS")

    assert "native-amount" not in row


def test_a_non_eur_holding_shows_its_native_figures(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(_holding("AAPL", currency="USD"),), totals=_totals()
    )

    row = _holding_row(_page(test_client), "AAPL")

    assert "200.00 USD" in _cell(row, "Avg cost")
    assert "2,400.00 USD" in _cell(row, "Market value")


def test_a_mixed_currency_holding_says_so_in_its_native_cells(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    accounts = (_breakdown("A", currency="EUR"), _breakdown("B", currency="GBP"))
    fake_portfolio.holdings = Holdings(
        holdings=(_holding(accounts=accounts, mixed=True),), totals=_totals()
    )

    row = _holding_row(_page(test_client), "IWDA.AS")

    assert "Mixed currencies" in _cell(row, "Avg cost")
    assert "Mixed currencies" in _cell(row, "Market value")
    assert "€1,200.00" in _cell(row, "Market value")


def test_net_zero_units_show_no_average_cost(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding(average_cost=None),), totals=_totals())

    row = _holding_row(_page(test_client), "IWDA.AS")

    assert "—" in _cell(row, "Avg cost")


def test_holdings_keep_portfolios_order(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(_holding("B.AS"), _holding("A.AS")), totals=_totals()
    )

    html = _page(test_client)

    assert html.index(">B.AS<") < html.index(">A.AS<")


# --- holdings waiting for prices ----------------------------------------------------------------


def test_unpriced_row_has_no_weight_and_takes_none_from_priced_rows(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(_holding("IWDA.AS"), _holding("NEW.DE", value=None)), totals=_totals()
    )

    html = _page(test_client)

    assert "100.00%" in _cell(_holding_row(html, "IWDA.AS"), "Weight")
    unpriced = _holding_row(html, "NEW.DE")
    assert _cell(unpriced, "Weight") is None
    assert "Waiting for prices" in _cell(unpriced, "Market value")
    assert "€100.00" in _cell(unpriced, "Avg cost")


@pytest.mark.parametrize(
    ("status", "note"),
    [
        ("pending", "Prices loading"),
        ("backfilling", "Prices loading"),
        ("failed", "Prices couldn&#39;t be loaded"),
        ("ready", "Waiting for prices"),
    ],
)
def test_unpriced_row_says_where_its_tickers_loading_stands(client, status, note):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding("NEW.DE", value=None),), totals=None)
    fake_portfolio.tracked_statuses = {"NEW.DE": status}

    row = _holding_row(_page(test_client), "NEW.DE")

    assert note in row


def test_ticker_statuses_are_asked_only_about_unpriced_holdings(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(_holding("IWDA.AS"), _holding("NEW.DE", value=None)), totals=_totals()
    )

    _page(test_client)

    assert ("list_tracked_tickers", ("NEW.DE",)) in fake_portfolio.calls
    assert not any(
        c[0] == "list_tracked_tickers" and "IWDA.AS" in c[1] for c in fake_portfolio.calls
    )


def test_no_status_call_when_every_holding_is_priced(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding(),), totals=_totals())

    _page(test_client)

    assert not any(c[0] == "list_tracked_tickers" for c in fake_portfolio.calls)


def test_a_status_failure_still_renders_the_row(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding("NEW.DE", value=None),), totals=None)
    fake_portfolio.list_tracked_tickers_error = PortfolioUnavailable("down")

    assert "Waiting for prices" in _holding_row(_page(test_client), "NEW.DE")


# --- the per-account breakdown ------------------------------------------------------------------


def test_each_holding_has_a_breakdown_of_its_accounts(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    accounts = (
        _breakdown("A", quantity="120", cost="9600", value="12000"),
        _breakdown("my isa & co", quantity="20", cost="1900", value="2000"),
    )
    fake_portfolio.holdings = Holdings(
        holdings=(_holding(accounts=accounts, value="14000", cost="11500"),), totals=_totals()
    )

    details = _breakdown_of(_page(test_client), "IWDA.AS")

    assert "<summary>In 2 accounts</summary>" in details
    assert 'href="/accounts/holdings?account=A"' in details
    assert 'href="/accounts/holdings?account=my%20isa%20%26%20co"' in details
    assert "80.00 EUR" in details  # account A's own average cost
    assert "€9,600.00" in details and "€12,000.00" in details and "+€2,400.00" in details


def test_a_single_account_holding_still_says_which_account(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding(),), totals=_totals())

    details = _breakdown_of(_page(test_client), "IWDA.AS")

    assert "<summary>In 1 account</summary>" in details
    assert 'href="/accounts/holdings?account=A"' in details


def test_breakdown_shows_each_accounts_own_currency(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    accounts = (
        _breakdown("A", currency="EUR", quantity="10", cost="500"),
        _breakdown("B", currency="GBP", quantity="10", cost="400"),
    )
    fake_portfolio.holdings = Holdings(
        holdings=(_holding(accounts=accounts, mixed=True),), totals=_totals()
    )

    details = _breakdown_of(_page(test_client), "IWDA.AS")

    assert "50.00 EUR" in details and "40.00 GBP" in details


def test_breakdown_of_an_account_without_prices_shows_dashes(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    accounts = (_breakdown("A", value=None),)
    fake_portfolio.holdings = Holdings(
        holdings=(_holding(accounts=accounts, value=None),), totals=None
    )

    details = _breakdown_of(_page(test_client), "IWDA.AS")

    market_value = _cell(details, "Market value")
    assert market_value is not None and "—" in market_value
    assert "—" in _cell(details, "Unrealized return")


# --- unmapped instruments -----------------------------------------------------------------------


def test_unmapped_warning_covers_every_broker_once(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding(),), totals=_totals())
    fake_portfolio.unmapped = [
        UnmappedInstrument("SYMBOL", "SXR8", "globex"),
        UnmappedInstrument("ISIN", "IE00B4L5Y983", None),
        UnmappedInstrument("SYMBOL", "SXR8", "acme"),
    ]

    html = _page(test_client)

    notice = re.search(r'<div id="unmapped-notice">.*?</div>\s*</div>', html, re.S).group(0)
    assert notice.count("SXR8") == 1
    assert notice.index("ISIN IE00B4L5Y983") < notice.index("SYMBOL SXR8")
    assert "An admin needs to map them." in notice


def test_no_unmapped_warning_when_nothing_is_unmapped(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    assert 'id="unmapped-notice"' not in _page(test_client)


# --- empty and error states ---------------------------------------------------------------------


def test_no_accounts_shows_the_first_run_card(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = []

    html = _page(test_client)

    assert "No accounts yet" in html
    assert 'href="/accounts"' in html
    assert "holdings-table" not in html


def test_accounts_but_no_holdings_says_so(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert "No holdings yet" in html
    assert "holdings-table" not in html
    assert "No accounts yet" not in html


def test_closed_positions_dividends_still_show_in_the_tiles_with_no_holdings(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(), totals=_totals(dividends_received_eur=Decimal("31.20"))
    )

    html = _page(test_client)

    assert "No holdings yet" in html
    assert "€31.20" in _tile(html, "Dividends received")


def test_a_holdings_failure_shows_a_notice_not_an_empty_table(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_holdings_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert "Couldn't load your holdings" in html
    assert "No holdings yet" not in html
    assert "holdings-table" not in html
    assert _tile(html, "Market value") is None


def test_an_accounts_failure_is_never_the_first_run_card(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_error = PortfolioUnavailable("down")
    fake_portfolio.holdings = Holdings(holdings=(_holding(),), totals=_totals())

    html = _page(test_client)

    assert "No accounts yet" not in html
    assert "Couldn't load everything" in html
    assert _holding_row(html, "IWDA.AS")


def test_an_unmapped_failure_shows_the_incomplete_banner(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_holding(),), totals=_totals())
    fake_portfolio.list_unmapped_instruments_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert "Couldn't load everything" in html
    assert _holding_row(html, "IWDA.AS")


def test_page_asks_portfolio_for_this_users_data(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    _page(test_client)

    assert {c for c in fake_portfolio.calls if c[0] != "list_tracked_tickers"} == {
        ("list_accounts", "u1"),
        ("list_holdings", "u1"),
        ("list_unmapped_instruments", "u1"),
    }
