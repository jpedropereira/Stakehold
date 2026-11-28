"""UX-10: /labs's growth-period and comparison-window switches swap only their own region.

A switch is still a link, back at its section, so it works without JS. With htmx it fetches the
same URL, and the route answers an htmx request aimed at a region with that region alone, plus
out-of-band copies of every other element that carries the period or the window in its query
(the other switch, the benchmark form's and the mix form's hidden inputs), so none of them goes
stale. A plain request, a history restore and any other target get the full page.

Its own minimal fakes, copied from test_labs_risk_page.py: only the calls this page makes.
"""

import html as html_lib
import json
import logging
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
    BACKTEST_PERIODS,
    RELATIVE_FIGURE_KEYS,
    RISK_FIGURE_KEYS,
    Backtest,
    BacktestAssetStatus,
    BacktestBenchmark,
    BacktestGrowth,
    BacktestPeriodReturn,
    BacktestRisk,
    BacktestRow,
    BacktestRowRisk,
    BacktestWindowRisk,
    DrawdownSeries,
    Holding,
    Holdings,
    RiskFigure,
    RiskFreeStatus,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
MINUS = "−"
END = date(2026, 10, 9)
BENCHMARK = BacktestBenchmark(
    ticker="VWCE.DE",
    available=True,
    reason=None,
    first_date=date(2019, 7, 29),
    growth=None,
    growth_reason="history starts after the growth period",
)
COMMON_START = date(2021, 11, 1)
LIMITED = "limited by AAPL (history from 2021-11-01)"


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
        self.backtest_result: Backtest | None = None
        self.backtest_calls: list[tuple] = []
        self.benchmarks: list[str] = []

    async def list_tickers(self):
        return ["IWDA.AS", "AAPL"]

    # {ticker: display_name} for tickers data-store tracks (UX-2); a test sets its own.
    tracked_names: dict[str, str] = {}
    tracked_error: Exception | None = None

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        if self.tracked_error:
            raise self.tracked_error
        return [
            SimpleNamespace(ticker=ticker, display_name=name, status="ready")
            for ticker, name in self.tracked_names.items()
        ]

    async def list_holdings(self, *, user_id):
        return self.holdings

    backtest_error: Exception | None = None

    async def list_saved_labs(self, *, user_id):
        # Saved labs (LF-1) are test_saved_labs_pages.py's; none here.
        return []

    async def backtest(self, *, assets, period, benchmark):
        self.backtest_calls.append((tuple(assets), period))
        self.benchmarks.append(benchmark)
        if self.backtest_error:
            raise self.backtest_error
        return self.backtest_result


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
        yield test_client, fake_portfolio


# --- fixtures: a backtest of IWDA.AS and AAPL, with risk ------------------------------------


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
        price_date=END,
        market_value_eur=Decimal(value),
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


def _row(ticker, weight):
    period = BacktestPeriodReturn(
        start=date(2025, 10, 9), end=END, covered=True, return_pct=Decimal("0.01"), gaps=0
    )
    return BacktestRow(
        ticker=ticker, weight=Decimal(weight), periods={p: period for p in BACKTEST_PERIODS}
    )


def _figure(value, *, frequency="daily", n_obs=252, flags=(), reason=None, se=None):
    return RiskFigure(
        value=value,
        window_start=date(2025, 10, 9),
        window_end=END,
        frequency=frequency,
        q=252.0 if frequency == "daily" else 52.0,
        n_obs=n_obs,
        n_gaps=0,
        flags=tuple(flags),
        reason=reason,
        standard_error=se,
    )


def _covered(start=date(2025, 10, 9), relative=None, **figures):
    weekly = {"frequency": "weekly", "n_obs": 52}
    all_figures = {key: _figure(0.01) for key in RISK_FIGURE_KEYS}
    all_figures.update(
        cagr=_figure(0.0712),
        volatility=_figure(0.1543, **weekly),
        max_drawdown=_figure(-0.184),
        sharpe=_figure(0.8649, se=0.5912, **weekly),
        sortino=_figure(1.234, **weekly),
        calmar=_figure(0.39, flags=("short_window",)),
    )
    all_figures.update(figures)
    return BacktestWindowRisk(
        start=start,
        end=END,
        covered=True,
        reason=None,
        figures=all_figures,
        peak_date=None,
        trough_date=None,
        recovery_date=None,
        max_days_under_water=None,
        relative=(
            {key: _figure(0.5, **weekly) for key in RELATIVE_FIGURE_KEYS}
            if relative is None
            else relative
        ),
    )


def _uncovered(reason=LIMITED):
    return BacktestWindowRisk(
        start=None,
        end=END,
        covered=False,
        reason=reason,
        figures={key: None for key in RISK_FIGURE_KEYS},
        peak_date=None,
        trough_date=None,
        recovery_date=None,
        max_days_under_water=None,
        relative=None,
    )


def _risk_row(ticker, **windows):
    all_windows = {
        "1Y": _covered(),
        "3Y": _covered(start=date(2023, 10, 9)),
        "5Y": _uncovered(),
        "10Y": _uncovered(),
        "Max": _covered(start=COMMON_START),
    }
    all_windows.update(windows)
    return BacktestRowRisk(ticker=ticker, windows=all_windows)


_DD_DATES = (date(2025, 10, 9), date(2025, 10, 10), date(2025, 10, 13))


def _risk(*, mix=None, assets=None, risk_free=None, drawdown="default", benchmark=None):
    return BacktestRisk(
        label="buy & hold (drifting)",
        end_date=END,
        common_start=COMMON_START,
        limited_by=("AAPL",),
        risk_free=risk_free
        or RiskFreeStatus(available=True, reason=None, first_date=date(1999, 1, 4)),
        benchmark=benchmark or BENCHMARK,
        mix=mix or _risk_row(None),
        assets=(
            tuple(assets)
            if assets is not None
            else (
                _risk_row("IWDA.AS"),
                _risk_row("AAPL", **{"1Y": _covered(cagr=_figure(-0.031))}),
            )
        ),
        drawdown=(
            DrawdownSeries(period="1Y", dates=_DD_DATES, values=(0.0, None, -0.0421))
            if drawdown == "default"
            else drawdown
        ),
    )


def _with_risk(fake_portfolio, risk=None):
    fake_portfolio.holdings = Holdings(
        holdings=(_holding("IWDA.AS", "750"), _holding("AAPL", "250")), totals=None
    )
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=_row(None, "1"),
        assets=(_row("IWDA.AS", "0.75"), _row("AAPL", "0.25")),
        growth=BacktestGrowth(
            period="1Y",
            dates=_DD_DATES,
            mix=(Decimal("0"), Decimal("0.01"), Decimal("-0.02")),
            assets={"IWDA.AS": None, "AAPL": None},
        ),
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "included", None),
        ),
        risk=_risk() if risk is None else risk,
    )


# --- helpers ----------------------------------------------------------------------------------

HTMX = {"HX-Request": "true"}


def _get(test_client, query="", target=None, **headers):
    sent = dict(headers)
    if target is not None:
        sent.update(HTMX, **{"HX-Target": target})
    response = test_client.get("/labs" + query, headers=sent)
    assert response.status_code == 200
    return response


def _open_tag(html, element_id):
    match = re.search(rf'<[a-z]+ [^>]*\bid="{element_id}"[^>]*>', html)
    assert match, f"no element #{element_id}"
    return match.group(0)


def _group(html, element_id):
    match = re.search(rf'<div class="segmented[^"]*" id="{element_id}".*?</div>', html, re.S)
    assert match, f"no group #{element_id}"
    return match.group(0)


def _kept(html, element_id):
    match = re.search(rf'<div id="{element_id}".*?</div>', html, re.S)
    assert match, f"no hidden inputs #{element_id}"
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', match.group(0)))


def _options(group):
    return re.findall(r"<a [^>]*>", group)


def _attr(tag, name):
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return None if match is None else html_lib.unescape(match.group(1))


def _current(group):
    return re.findall(r'aria-current="true"[^>]*>(?:<span class="mono">)?([^<]+)<', group)


# --- the switches -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "group_id, region, param",
    [
        ("labs-periods", "labs-growth", "period"),
        ("labs-risk-windows", "labs-risk-compare", "risk_window"),
    ],
)
def test_each_option_is_a_link_to_its_section_that_htmx_swaps_in_place(
    client, group_id, region, param
):
    test_client, fake = client
    _with_risk(fake)

    page = _get(test_client).text

    options = _options(_group(page, group_id))
    assert options
    for option in options:
        href = _attr(option, "href")
        assert href.startswith("/labs?") and href.endswith(f"#{region}")
        assert f"{param}=" in href
        assert _attr(option, "hx-get") == href.split("#")[0]
        assert _attr(option, "hx-push-url") == href
        assert _attr(option, "hx-target") == f"#{region}"
        assert _attr(option, "hx-select") == f"#{region}"


def test_the_regions_are_marked_and_announce_what_they_show(client):
    test_client, fake = client
    _with_risk(fake)

    page = _get(test_client, "?period=5Y&risk_window=3Y").text

    growth = _open_tag(page, "labs-growth")
    compare = _open_tag(page, "labs-risk-compare")
    assert "data-swap-region" in growth and "data-swap-region" in compare
    assert _attr(growth, "data-swap-announce") == "Growth over 5Y"
    assert _attr(compare, "data-swap-announce") == "Comparison over 3Y"


def test_each_region_has_a_slot_for_a_failed_request(client):
    """Under its heading: static/htmx-error-swap.js puts the failure banner there."""
    test_client, fake = client
    _with_risk(fake)

    page = _get(test_client).text

    for region in ("labs-growth", "labs-risk-compare"):
        after = page.split(_open_tag(page, region), 1)[1]
        assert re.match(r"\s*<h[23][^>]*>.*?</h[23]>\s*<div data-swap-error></div>", after, re.S)


def test_no_script_is_loaded_inside_a_region(client):
    """htmx runs a swapped-in <script>, which would load Chart.js and the chart scripts again.
    The JSON data islands stay: htmx doesn't run them."""
    test_client, fake = client
    _with_risk(fake)

    page = _get(test_client).text
    swap = _get(test_client, "?period=5Y", target="labs-growth").text

    assert "<script src" not in swap
    assert '<script id="labs-chart-data" type="application/json">' in swap
    for script in ("vendor/chart.min.js", "labs_chart.js", "labs_drawdown_chart.js"):
        assert script in page


# --- the swap response ------------------------------------------------------------------------


def test_a_period_swap_sends_only_the_growth_region_for_that_period(client):
    test_client, fake = client
    _with_risk(fake)

    swap = _get(test_client, "?period=5Y", target="labs-growth").text

    assert "<html" not in swap and "<header" not in swap
    assert 'id="labs-form"' not in swap and 'id="labs-returns-title"' not in swap
    assert 'id="labs-risk-compare"' not in swap
    assert swap.lstrip().startswith(_open_tag(swap, "labs-growth"))
    assert _current(_group(swap, "labs-periods")) == ["5Y"]
    assert fake.backtest_calls[-1][1] == "5Y"


def test_a_period_swap_keeps_every_other_carrier_of_the_period_in_step(client):
    test_client, fake = client
    _with_risk(fake)

    swap = _get(test_client, "?period=5Y&risk_window=3Y", target="labs-growth").text

    windows = _group(swap, "labs-risk-windows")
    assert 'hx-swap-oob="true"' in _open_tag(swap, "labs-risk-windows")
    assert all("period=5Y" in _attr(o, "href") for o in _options(windows))
    assert _current(windows) == ["3Y"]
    for kept in ("labs-benchmark-kept", "labs-form-kept"):
        assert 'hx-swap-oob="true"' in _open_tag(swap, kept)
        assert _kept(swap, kept)["period"] == "5Y"
        assert _kept(swap, kept)["risk_window"] == "3Y"


def test_a_window_swap_sends_only_the_comparison_and_the_carriers(client):
    test_client, fake = client
    _with_risk(fake)

    swap = _get(test_client, "?period=5Y&risk_window=3Y", target="labs-risk-compare").text

    assert "<html" not in swap
    assert swap.lstrip().startswith(_open_tag(swap, "labs-risk-compare"))
    assert 'id="labs-growth"' not in swap
    assert _current(_group(swap, "labs-risk-windows")) == ["3Y"]
    periods = _group(swap, "labs-periods")
    assert 'hx-swap-oob="true"' in _open_tag(swap, "labs-periods")
    assert all("risk_window=3Y" in _attr(o, "href") for o in _options(periods))
    assert _current(periods) == ["5Y"]
    assert _kept(swap, "labs-form-kept")["risk_window"] == "3Y"
    assert _kept(swap, "labs-benchmark-kept")["risk_window"] == "3Y"


def test_the_benchmark_rides_along_in_the_carriers(client):
    test_client, fake = client
    _with_risk(fake)

    swap = _get(test_client, "?period=5Y&benchmark=IWDA.AS", target="labs-growth").text

    assert _kept(swap, "labs-form-kept")["benchmark"] == "IWDA.AS"
    # The benchmark form's own field sends the benchmark, so its hidden inputs leave it out.
    assert "benchmark" not in _kept(swap, "labs-benchmark-kept")
    assert fake.benchmarks[-1] == "IWDA.AS"


def test_an_edited_mix_rides_along_in_the_switches(client):
    test_client, fake = client
    _with_risk(fake)

    swap = _get(test_client, "?ticker=IWDA.AS&weight=100&period=5Y", target="labs-growth").text

    windows = _group(swap, "labs-risk-windows")
    assert all("ticker=IWDA.AS" in _attr(o, "href") for o in _options(windows))
    assert fake.backtest_calls[-1] == ((("IWDA.AS", Decimal("1")),), "5Y")


def test_a_failed_backtest_shows_its_notice_inside_the_region(client):
    test_client, fake = client
    _with_risk(fake)
    fake.backtest_error = PortfolioUnavailable("down")

    for target in ("labs-growth", "labs-risk-compare"):
        swap = _get(test_client, "?period=5Y", target=target).text

        assert swap.lstrip().startswith(_open_tag(swap, target))
        assert "Couldn&#39;t run the backtest." in swap or "Couldn't run the backtest." in swap
        assert "<html" not in swap


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"HX-Request": "true"},
        {"HX-Request": "true", "HX-Target": "labs-form"},
        {"HX-Request": "true", "HX-Target": "labs-growth", "HX-History-Restore-Request": "true"},
    ],
    ids=["plain", "htmx-no-target", "unknown-target", "history-restore"],
)
def test_anything_else_gets_the_full_page(client, headers):
    test_client, fake = client
    _with_risk(fake)

    page = _get(test_client, "?period=5Y", **headers).text

    assert "<html" in page
    assert 'id="labs-growth"' in page and 'id="labs-risk-compare"' in page
    assert "hx-swap-oob" not in page


@pytest.mark.parametrize("target", [None, "labs-growth"])
def test_every_response_varies_on_the_htmx_headers(client, target):
    """One URL answers with a region or a page, so a cache must keep the two apart."""
    test_client, fake = client
    _with_risk(fake)

    vary = _get(test_client, "?period=5Y", target=target).headers["vary"]

    assert "HX-Request" in vary and "HX-Target" in vary


def test_a_swap_is_logged(client, caplog):
    test_client, fake = client
    _with_risk(fake)

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        _get(test_client, "?period=5Y", target="labs-growth")

    assert "labs: sending #labs-growth only." in caplog.messages


# --- what every page carries for a swap -----------------------------------------------------


def test_htmx_keeps_no_snapshot_of_the_page_and_reloads_on_back(client):
    """A pushed URL would otherwise snapshot the page into localStorage, figures and all, where
    they outlive logout, and Back would restore it with blank charts. Back reloads instead."""
    test_client, fake = client
    _with_risk(fake)

    page = _get(test_client).text

    config = json.loads(
        html_lib.unescape(
            re.search(r'<meta name="htmx-config" content=\'([^\']*)\'>', page).group(1)
        )
    )
    assert config["historyCacheSize"] == 0
    assert config["refreshOnHistoryMiss"] is True
    assert config["includeIndicatorStyles"] is False
    # Not "style": htmx would set it on a swapped-in canvas with setAttribute, which the CSP
    # blocks.
    assert config["attributesToSettle"] == ["class"]


def test_one_polite_live_region_outside_every_region_says_what_changed(client):
    test_client, fake = client
    _with_risk(fake)

    page = _get(test_client).text

    assert page.count('id="swap-status"') == 1
    status = _open_tag(page, "swap-status")
    assert 'role="status"' in status and 'class="visually-hidden"' in status
    assert page.index('id="swap-status"') > page.index("</main>")
    assert re.search(r'<script src="/static/section_swap\.js\?v=\w+" defer></script>', page)
