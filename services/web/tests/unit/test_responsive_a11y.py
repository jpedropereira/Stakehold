"""UI-13: the responsive and accessibility pass.

Phone-width behaviour itself is checked in a browser by scripts/review_pages.py. These tests pin
the markup and CSS that behaviour relies on:

- the holdings and transactions tables carry a label on every figure, so they can stack;
- they carry explicit table roles, because a stacked table's `display` change drops its native
  semantics in WebKit;
- the admin tables, which still scroll, can be reached by keyboard;
- the review script covers every page.

Kept apart from test_routes.py, which is over the repo's 64KB file limit. Like
test_home_page.py, it carries its own minimal fakes: only the calls these pages make.
"""

import html as html_module
import importlib.util
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api import deletion, holdings, overview, portfolio, routes, tickers
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    Account,
    Holding,
    HoldingAccount,
    Holdings,
    PortfolioReturns,
    Position,
    Return,
    TickerMapping,
    TrackedTicker,
    Transaction,
    UnmappedInstrument,
)
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
_WEB_DIR = Path(__file__).resolve().parents[2]
_WEB_CSS_PATH = _WEB_DIR / "src" / "web" / "static" / "web.css"
_REVIEW_SCRIPT_PATH = _WEB_DIR / "scripts" / "review_pages.py"


class FakeAuthClient:
    """Anonymous until `_logged_in` sets a result."""

    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.list_accounts_result = [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]
        self.positions: list[Position] = []
        self.latest_returns: list[Return] = []
        self.transactions: list[Transaction] = []
        self.mappings: list[TickerMapping] = []
        self.unmapped: list[UnmappedInstrument] = []
        self.tracked: list[TrackedTicker] = []
        self.holdings = Holdings(holdings=(), totals=None)

    async def list_accounts(self, *, user_id):
        return self.list_accounts_result

    async def list_holdings(self, *, user_id):
        return self.holdings

    async def list_portfolio_returns(self, *, user_id):
        return PortfolioReturns(series=(), by_ticker=())

    async def list_unmapped_instruments(self, *, user_id):
        return []

    async def list_positions(self, *, user_id, account_label):
        return self.positions

    async def list_returns(self, *, user_id, account_label):
        return []

    async def list_growth(self, *, user_id, account_label):
        return []

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        return self.tracked

    async def list_latest_returns(self, *, user_id, account_label):
        return self.latest_returns

    async def list_transactions(self, *, user_id, account_label):
        return self.transactions

    async def list_ticker_mappings(self):
        return self.mappings

    async def list_all_unmapped_instruments(self):
        return self.unmapped

    async def list_tickers(self):
        return ["VWCE.DE"]

    async def list_brokers(self):
        return []


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


def _logged_in(test_client, fake_auth, *, is_admin=False):
    user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann", is_admin=is_admin)
    fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


def _position(ticker) -> Position:
    return Position(
        account_id=1,
        canonical_ticker=ticker,
        as_of_date=date(2026, 1, 5),
        quantity=Decimal("10"),
        average_cost_native=Decimal("150"),
        native_currency="EUR",
        average_cost_eur=Decimal("150"),
        cost_basis_eur=Decimal("1500"),
    )


def _latest(ticker) -> Return:
    return Return(
        account_id=1,
        canonical_ticker=ticker,
        price_date=date(2026, 1, 5),
        quantity=Decimal("10"),
        market_value_native=Decimal("1600"),
        market_value_eur=Decimal("1600"),
        cost_basis_eur=Decimal("1500"),
        unrealized_return_eur=Decimal("100"),
        unrealized_return_pct=Decimal("0.0667"),
        dividends_received_eur=Decimal("0"),
        total_return_eur=Decimal("100"),
        total_return_pct=Decimal("0.0667"),
    )


def _buy() -> Transaction:
    return Transaction(
        account_id=1,
        broker_transaction_ref="buy-1",
        transaction_type="BUY",
        trade_datetime=datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc),
        instrument_identifier="VWCE.DE",
        instrument_identifier_kind="TICKER",
        quantity=Decimal("10"),
        price=Decimal("112.34"),
        native_currency="EUR",
        native_amount=Decimal("-1124.40"),
        eur_amount=Decimal("-1124.40"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=Decimal("1"),
    )


def _table(page_html, css_class):
    match = re.search(
        rf'<table [^>]*class="[^"]*\b{css_class}\b[^"]*"[^>]*>.*?</table>', page_html, re.S
    )
    assert match, f"no table with class {css_class}"
    return match.group(0)


def _tags(table_html, tag):
    """The opening tags of every `tag` element in the table."""
    return re.findall(rf"<{tag}\b[^>]*>", table_html)


def _attr(tag_html, name):
    match = re.search(rf'\b{name}="([^"]*)"', tag_html)
    return None if match is None else html_module.unescape(match.group(1))


def _body_rows(table_html):
    tbody = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", table_html, re.S).group(1)
    return re.findall(r"<tr\b[^>]*>(.*?)</tr>", tbody, re.S)


def _assert_stackable(table_html, expected_labels):
    """The table can stack: explicit roles throughout, and every cell after a row's first
    labelled like its column, in column order."""
    assert "stack-table" in _attr(_tags(table_html, "table")[0], "class")
    assert _attr(_tags(table_html, "table")[0], "role") == "table"
    for tag in ("thead", "tbody"):
        assert [_attr(t, "role") for t in _tags(table_html, tag)] == ["rowgroup"]
    assert {_attr(t, "role") for t in _tags(table_html, "tr")} == {"row"}
    assert {_attr(t, "role") for t in _tags(table_html, "th")} == {"columnheader"}
    assert {_attr(t, "role") for t in _tags(table_html, "td")} == {"cell"}
    rows = _body_rows(table_html)
    assert rows
    for row in rows:
        cells = re.findall(r"<td\b[^>]*>", row)
        assert _attr(cells[0], "data-label") is None, "the first cell is the row's heading"
        assert [_attr(c, "data-label") for c in cells[1:]] == expected_labels


_HOLDINGS_LABELS = [
    "Quantity",
    "Avg cost",
    "Market value",
    "Weight",
    "Unrealized return",
    "Unrealized return (%)",
]


def test_holdings_table_can_stack_at_phone_width(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.positions = [_position("AAPL"), _position("MSFT")]
    fake_portfolio.latest_returns = [_latest("AAPL"), _latest("MSFT")]

    page_html = test_client.get("/accounts/holdings?account=main").text

    _assert_stackable(_table(page_html, "holdings-table"), _HOLDINGS_LABELS)


def test_holdings_waiting_for_prices_cell_is_labelled(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.positions = [_position("AAPL"), _position("MSFT")]
    fake_portfolio.latest_returns = [_latest("AAPL")]

    table = _table(test_client.get("/accounts/holdings?account=main").text, "holdings-table")

    waiting = next(row for row in _body_rows(table) if "MSFT" in row)
    labels = [_attr(c, "data-label") for c in re.findall(r"<td\b[^>]*>", waiting)]
    assert labels == [None, "Quantity", "Avg cost", "Market value"]
    assert "Waiting for prices" in waiting


def _combined_holding(ticker, *, priced=True) -> Holding:
    value = Decimal("1600") if priced else None
    account = HoldingAccount(
        account_label="main",
        quantity=Decimal("10"),
        native_currency="EUR",
        average_cost_native=Decimal("150"),
        cost_basis_eur=Decimal("1500"),
        market_value_native=value,
        market_value_eur=value,
        unrealized_return_eur=None if value is None else Decimal("100"),
    )
    return Holding(
        canonical_ticker=ticker,
        quantity=Decimal("10"),
        average_cost_eur=Decimal("150"),
        cost_basis_eur=Decimal("1500"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=Decimal("150"),
        market_value_native=value,
        priced=priced,
        price_date=date(2026, 1, 5) if priced else None,
        market_value_eur=value,
        unrealized_return_eur=None if value is None else Decimal("100"),
        unrealized_return_pct=None if value is None else Decimal("0.0667"),
        dividends_received_eur=None if value is None else Decimal("0"),
        total_return_eur=None if value is None else Decimal("100"),
        total_return_pct=None if value is None else Decimal("0.0667"),
        accounts=(account,),
    )


_PORTFOLIO_LABELS = [
    "Units",
    "Avg cost",
    "Market value",
    "Weight",
    "Unrealized return",
    "Unrealized return (%)",
    "Total return",
    "Total return (%)",
    "Growth %",
]


def _rows_with_class(page_html, css_class):
    return re.findall(rf'<tr role="row" class="{css_class}">(.*?)</tr>', page_html, re.S)


def test_portfolio_table_can_stack_at_phone_width(client):
    """/portfolio's main rows are labelled like the per-account table's. Each is followed by a
    breakdown row whose single cell spans the table and holds a stackable table of its own."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(_combined_holding("AAPL"), _combined_holding("MSFT")), totals=None
    )

    page_html = test_client.get("/portfolio").text

    outer = _tags(page_html, "table")[0]
    assert "stack-table" in _attr(outer, "class") and _attr(outer, "role") == "table"
    rows = _rows_with_class(page_html, "holding-row")
    assert len(rows) == 2
    for row in rows:
        cells = re.findall(r"<td\b[^>]*>", row)
        assert _attr(cells[0], "data-label") is None
        assert [_attr(c, "data-label") for c in cells[1:]] == _PORTFOLIO_LABELS
    breakdown_cells = re.findall(
        r'<tr role="row" class="breakdown-row">\s*(<td\b[^>]*>)', page_html
    )
    assert [_attr(c, "colspan") for c in breakdown_cells] == ["10", "10"]
    assert all(_attr(c, "role") == "cell" for c in breakdown_cells)
    inner = [t for t in _tags(page_html, "table") if "breakdown-table" in (_attr(t, "class") or "")]
    assert len(inner) == 2
    assert all("stack-table" in _attr(t, "class") and _attr(t, "role") == "table" for t in inner)


def test_portfolio_table_scrolls_in_its_own_named_region(client):
    """Ten columns don't fit between phone and desktop width, where the table doesn't stack yet:
    it scrolls in a box a keyboard can reach and a screen reader can name, not the page."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(_combined_holding("AAPL"),), totals=None)

    page_html = test_client.get("/portfolio").text

    region = re.search(r'(<div class="table-scroll"[^>]*>)\s*<table class="([^"]*)"', page_html)
    assert region and "portfolio-table" in region.group(2)
    assert _attr(region.group(1), "tabindex") == "0"
    assert _attr(region.group(1), "role") == "region"
    assert _attr(region.group(1), "aria-label") == "Holdings"


def test_portfolio_waiting_for_prices_cell_is_labelled(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(
        holdings=(_combined_holding("AAPL"), _combined_holding("MSFT", priced=False)),
        totals=None,
    )

    page_html = test_client.get("/portfolio").text

    waiting = next(r for r in _rows_with_class(page_html, "holding-row") if "MSFT" in r)
    labels = [_attr(c, "data-label") for c in re.findall(r"<td\b[^>]*>", waiting)]
    assert labels == [None, "Units", "Avg cost", "Market value"]
    assert "Waiting for prices" in waiting


def test_transactions_table_can_stack_at_phone_width(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.transactions = [_buy()]

    page_html = test_client.get("/transactions?account=main").text

    _assert_stackable(
        _table(page_html, "transactions-table"),
        # The Delete link names itself, so its cell needs no label.
        ["Type", "Instrument", "Quantity", "Price", "Amount", "Fees", "Reference", None],
    )


def test_stacked_holdings_table_needs_no_scroll_box(client):
    """A stacked table fits the width, and the holdings table fits its card above it."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.positions = [_position("AAPL")]
    fake_portfolio.latest_returns = [_latest("AAPL")]

    assert "table-scroll" not in test_client.get("/accounts/holdings?account=main").text


def test_transactions_table_scrolls_in_its_own_named_region(client):
    """LY-2: at full width the reference shows whole, and between phone and desktop width, where
    the table doesn't stack yet, its nine columns scroll in their box rather than the page."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.transactions = [_buy()]

    page_html = test_client.get("/transactions?account=main").text

    region = re.search(r'(<div class="table-scroll"[^>]*>)\s*<table class="([^"]*)"', page_html)
    assert region and "transactions-table" in region.group(2)
    assert _attr(region.group(1), "tabindex") == "0"
    assert _attr(region.group(1), "role") == "region"
    assert _attr(region.group(1), "aria-label") == "Transactions"


def _tracked(ticker) -> TrackedTicker:
    return TrackedTicker(
        ticker=ticker,
        display_name="Vanguard FTSE All-World",
        asset_type="etf",
        currency="EUR",
        exchange="XETRA",
        origin="request",
        status="ready",
        enabled=True,
        attempts=0,
        last_error=None,
        requested_by="u1",
        requested_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        ready_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )


def _mapping() -> TickerMapping:
    return TickerMapping(
        id=1,
        identifier_kind="ISIN",
        identifier="IE00BK5BQT80",
        broker=None,
        canonical_ticker="VWCE.DE",
        updated_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )


def _main_classes(page_html):
    return _attr(re.search(r'<main id="main"[^>]*>', page_html).group(0), "class").split()


@pytest.mark.parametrize(
    "path, is_admin",
    [
        ("/transactions?account=main", False),
        ("/tickers", False),
        ("/tickers", True),
        ("/admin/mappings", True),
        ("/portfolio", False),
    ],
)
def test_wide_table_pages_take_the_full_content_width(client, path, is_admin):
    """LY-2: the page, not each block, sets the width, so the account header, as-of line, tiles
    and wide cards can't drift apart."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth, is_admin=is_admin)
    fake_portfolio.transactions = [_buy()]
    fake_portfolio.holdings = Holdings(holdings=(_combined_holding("AAPL"),), totals=None)
    fake_portfolio.tracked = [_tracked("VWCE.DE")]
    fake_portfolio.mappings = [_mapping()]

    page_html = test_client.get(path).text

    assert _main_classes(page_html) == ["page", "page-full"]
    tables = re.findall(r'<section class="card card-wide"[^>]*>.*?</section>', page_html, re.S)
    assert any("<table" in section for section in tables)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/accounts",
        "/transactions/new?account=main",
        "/transactions/import?account=main",
    ],
)
def test_other_pages_keep_the_default_width(client, path):
    """Overview keeps the 880px column until LY-6 lays it out; forms keep their own cards.
    Account holdings went full width in LY-5 (test_account_holdings_page.py)."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    page_html = test_client.get(path).text

    assert _main_classes(page_html) == ["page"]


def test_wide_blocks_take_their_width_from_the_page():
    """No block repeats a pixel width of its own: the page variable sets all four."""
    css = _css()
    assert "max-width: 880px" not in css
    assert ".content-wide" not in css
    for selector in (".account-header", ".card-wide", ".as-of", ".summary-tiles"):
        rule = re.search(rf"\{selector} \{{([^}}]*)\}}", css)
        assert rule and "max-width: var(--page-wide)" in rule.group(1), selector


def test_admin_tables_scroll_in_a_keyboard_reachable_region(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth, is_admin=True)
    fake_portfolio.unmapped = [
        UnmappedInstrument(kind="ISIN", identifier="IE00B4L5Y983", broker=None)
    ]
    fake_portfolio.mappings = [
        TickerMapping(
            id=1,
            identifier_kind="ISIN",
            identifier="IE00BK5BQT80",
            broker=None,
            canonical_ticker="VWCE.DE",
            updated_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
    ]

    page_html = test_client.get("/admin/mappings").text

    regions = re.findall(r'<div [^>]*class="table-scroll"[^>]*>', page_html)
    assert len(regions) == 2
    for region in regions:
        assert _attr(region, "tabindex") == "0"
        assert _attr(region, "role") == "region"
    assert sorted(_attr(r, "aria-label") for r in regions) == [
        "Instruments waiting for a mapping",
        "Mappings",
    ]


def _css():
    return re.sub(r"/\*.*?\*/", "", _WEB_CSS_PATH.read_text(), flags=re.S)


def test_web_css_stacks_tables_at_phone_width():
    blocks = re.findall(r"@media \(max-width: 600px\) \{(.*?)\n\}", _css(), re.S)
    assert blocks, "no phone-width media block for stacked tables"
    assert ".stack-table" in blocks[0]


def test_stacked_table_labels_are_hidden_from_screen_readers():
    """The column header already names each cell for a screen reader; the drawn label carries
    empty alt text so it isn't read a second time."""
    assert re.search(r'content:\s*attr\(data-label\)\s*/\s*""', _css())


def test_table_scroll_contains_what_is_positioned_inside_it():
    """LY-2: the transactions table's visually-hidden Actions header is absolutely positioned. With
    no positioned ancestor inside the scroll box, it escaped the box's clipping and the page
    scrolled sideways at tablet width."""
    rule = re.search(r"\.table-scroll \{([^}]*)\}", _css())
    assert rule and "position: relative" in rule.group(1)


def test_table_scroll_regions_show_a_focus_ring():
    focus_rule = re.search(r"^:is\(([^)]*)\):focus-visible", _css(), re.M)
    assert focus_rule and ".table-scroll" in focus_rule.group(1)


def test_date_input_keeps_a_focus_ring_on_its_calendar_button():
    """Chrome's date/time input has a calendar button inside it, reached by Tab. While it has
    focus the input itself doesn't match :focus-visible, only :focus-within, so without its own
    rule the keyboard stop shows no ring at all."""
    rule = re.search(r'input\[type="datetime-local"\]:focus-within\s*\{([^}]*)\}', _css())
    assert rule, "no focus ring for a date input's calendar button"
    assert "var(--focus-ring)" in rule.group(1)


def _review_script():
    spec = importlib.util.spec_from_file_location("review_pages", _REVIEW_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_script_covers_every_page():
    """A page added later can't silently miss the screenshot and contrast review."""
    page_routes = {
        route.path
        for router in (
            routes.router,
            overview.router,
            deletion.router,
            tickers.router,
            holdings.router,
            portfolio.router,
        )
        for route in router.routes
        if "GET" in route.methods
    }

    reviewed = {page.path.split("?", 1)[0] for page in _review_script().PAGES}

    assert page_routes <= reviewed, f"not reviewed: {sorted(page_routes - reviewed)}"


def test_review_script_covers_every_chart_view_on_both_chart_pages():
    """LY-3: the chart is checked in each view it can show. Total return is the default, so it's
    the page with no click."""
    reviewed = {(page.path.split("?", 1)[0], page.click) for page in _review_script().PAGES}

    for path in ("/portfolio", "/accounts/holdings"):
        for click in (
            None,
            "[data-chart-view=market_value_eur]",
            "[data-chart-view=growth_pct]",
        ):
            assert (path, click) in reviewed, f"{path} not reviewed with click {click}"


def test_review_script_covers_phone_tablet_desktop_laptop_and_wide():
    """LY-1: the empty sides only show at 1920px, and tables overflowed between 600px and about
    990px unseen, so the review covers each class of screen. LY-4: laptop sits just under the
    split breakpoint (1480px), so both sides of it are reviewed."""
    assert _review_script().WIDTHS == {
        "phone": 360,
        "tablet": 768,
        "desktop": 1280,
        "laptop": 1440,
        "wide": 1920,
    }


def test_review_script_rejects_an_unknown_width(capsys):
    """--width narrows a run to some widths; a typo stops the run instead of reviewing nothing."""
    with pytest.raises(SystemExit) as stopped:
        _review_script().main(["--width", "nope"])

    assert stopped.value.code == 2
    assert "invalid choice: 'nope'" in capsys.readouterr().err
