"""GET /labs's risk panel and drawdown chart (RK-9): the mix's risk grid by window, the mix and
its assets compared over one window (`?risk_window=`), the notes that qualify them (no
risk-free rate, the asset that limits every window, windows that aren't covered), the
drawdown chart under the growth chart, and the log line.

Like test_labs_page.py, it carries its own minimal fakes: only the calls this page makes.
"""

import html as html_lib
import json
import logging
import re
from dataclasses import replace
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
    BACKTEST_RISK_WINDOWS,
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

    async def backtest(self, *, assets, period, benchmark):
        self.backtest_calls.append((tuple(assets), period))
        self.benchmarks.append(benchmark)
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


# --- page helpers ---------------------------------------------------------------------------


def _page(test_client, query=""):
    response = test_client.get("/labs" + query)
    assert response.status_code == 200
    return response.text


def _section(html):
    match = re.search(r'<section class="labs-risk" id="labs-risk".*?</section>', html, re.S)
    assert match, "no risk section"
    return match.group(0)


def _tables(html):
    return re.findall(r"<table .*?</table>", _section(html), re.S)


def _grid(html):
    return _tables(html)[0]


def _comparison(html):
    return _tables(html)[1]


def _grid_row(html, label):
    """The mix grid's row whose measure links to `label`."""
    for row in re.findall(r"<tr role=\"row\".*?</tr>", _grid(html), re.S):
        if f">{label}</a>" in row:
            return row
    raise AssertionError(f"no grid row for {label}")


def _cells(row):
    return re.findall(r"<td [^>]*>(.*?)</td>", row, re.S)


def _text(fragment):
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _links(html, nav_class):
    nav = re.search(rf'<div class="segmented {nav_class}".*?</div>', html, re.S).group(0)
    return [
        (label, href.replace("&amp;", "&"), bool(current))
        for href, current, label in re.findall(
            r'<a [^>]*href="([^"]*)"( aria-current="true")?[^>]*>(?:<span class="mono">)?(\w+)<',
            nav,
        )
    ]


def _drawdown_island(html):
    match = re.search(
        r'<script id="labs-drawdown-data" type="application/json">(.*?)</script>', html, re.S
    )
    return None if match is None else json.loads(match.group(1))


# --- the mix grid ---------------------------------------------------------------------------


def test_the_risk_section_is_labelled_historical_and_drifting(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    section = _text(_section(_page(test_client)))

    assert "buy & hold (drifting)" in section
    assert "historical" in section.lower()
    # R15: historical figures are not forecasts.
    assert "expected" not in section.lower()


def test_the_grid_has_a_column_per_window_with_its_start(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    header = re.search(r"<thead.*?</thead>", _grid(_page(test_client)), re.S).group(0)

    assert re.findall(r"<th [^>]*>(1Y|3Y|5Y|10Y|Max)", header) == list(BACKTEST_RISK_WINDOWS)
    assert 'from <span class="mono">2025-10-09</span>' in header
    assert 'from <span class="mono">2021-11-01</span>' in header


def test_the_grid_shows_each_measure_formatted(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)
    html = _page(test_client)

    assert _text(_cells(_grid_row(html, "CAGR"))[1]) == "+7.1%"
    assert _text(_cells(_grid_row(html, "Volatility"))[1]) == "15.4%"
    assert _text(_cells(_grid_row(html, "Max drawdown"))[1]) == f"{MINUS}18.4%"
    assert _text(_cells(_grid_row(html, "Sharpe"))[1]) == "0.86 ± 0.59"
    assert _text(_cells(_grid_row(html, "Sortino"))[1]) == "1.23"
    assert _text(_cells(_grid_row(html, "Calmar"))[1]) == "0.39 under 3 years"


def test_each_measure_names_its_frequency_and_sharpe_its_standard_error(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)
    html = _page(test_client)

    assert "weekly" in _text(_cells(_grid_row(html, "Volatility"))[0])
    assert "daily" in _text(_cells(_grid_row(html, "Max drawdown"))[0])
    assert "1 s.e." in _text(_cells(_grid_row(html, "Sharpe"))[0])


def test_a_drawdown_is_not_colored_as_a_loss(client):
    # Agreed for RK-9: forest and oxblood are for gains and losses, and a drawdown is neither.
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    row = _grid_row(_page(test_client), "Max drawdown")

    assert not re.search(r'class="[^"]*\b(gain|loss)\b', row)


def test_a_cagr_is_colored_as_a_return(client):
    test_client, fake_portfolio = client
    mix = _risk_row(None, **{"3Y": _covered(cagr=_figure(-0.02))})
    _with_risk(fake_portfolio, _risk(mix=mix))

    cells = _cells(_grid_row(_page(test_client), "CAGR"))

    assert 'class="mono gain"' in cells[1]
    assert 'class="mono loss"' in cells[2]


def test_a_greyed_figure_shows_its_reason(client):
    test_client, fake_portfolio = client
    reason = "insufficient history (have 40, need 52)"
    mix = _risk_row(
        None, **{"1Y": _covered(sortino=_figure(None, frequency="weekly", reason=reason))}
    )
    _with_risk(fake_portfolio, _risk(mix=mix))

    assert _text(_cells(_grid_row(_page(test_client), "Sortino"))[1]) == reason


def test_an_uncovered_window_reads_not_enough_history_and_names_why_once(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)
    html = _page(test_client)

    five = BACKTEST_RISK_WINDOWS.index("5Y") + 1
    assert _text(_cells(_grid_row(html, "CAGR"))[five]) == "Not enough history"
    section = _text(_section(html))
    assert f"5Y, 10Y: {LIMITED}." in section
    assert section.count(LIMITED) == 1


def test_the_grid_says_how_many_returns_each_window_used(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    row = re.search(
        r'<tr role="row" class="labs-risk-used".*?</tr>', _grid(_page(test_client)), re.S
    )

    assert _text(_cells(row.group(0))[1]) == "252 daily · 52 weekly"


def test_the_window_every_row_shares_names_the_asset_that_limits_it(client):
    # R10: the common window starts at the youngest asset's first day, and the page says whose.
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    section = _text(_section(_page(test_client)))

    assert "starts on or after 2021-11-01, the first day AAPL has a price" in section


def test_no_risk_free_rate_is_said_once(client):
    test_client, fake_portfolio = client
    risk_free = RiskFreeStatus(available=False, reason="no risk-free rate data", first_date=None)
    _with_risk(fake_portfolio, _risk(risk_free=risk_free))

    section = _text(_section(_page(test_client)))

    assert "Sharpe and Sortino can't be measured: no risk-free rate data." in section


def test_no_risk_section_without_risk_figures(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=fake_portfolio.backtest_result.mix,
        assets=fake_portfolio.backtest_result.assets,
        growth=fake_portfolio.backtest_result.growth,
        statuses=fake_portfolio.backtest_result.statuses,
        risk=None,
    )

    html = _page(test_client)

    assert 'id="labs-risk"' not in html
    assert _drawdown_island(html) is None


# --- the comparison over one window ---------------------------------------------------------


def test_the_comparison_shows_1y_by_default_with_the_mix_then_each_asset(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)
    html = _page(test_client)

    rows = re.findall(r"<tr role=\"row\".*?</tr>", _comparison(html), re.S)[1:]

    assert [_text(_cells(row)[0]) for row in rows] == ["Mix", "IWDA.AS", "AAPL"]
    assert _text(_cells(rows[2])[1]) == f"{MINUS}3.1%"
    assert [label for label, _, current in _links(html, "labs-risk-windows") if current] == ["1Y"]


def test_risk_window_picks_the_comparison(client):
    test_client, fake_portfolio = client
    assets = [_risk_row("IWDA.AS", **{"3Y": _covered(cagr=_figure(0.123))})]
    _with_risk(fake_portfolio, _risk(assets=assets))

    html = _page(test_client, "?risk_window=3Y")

    assert "+12.3%" in _comparison(html)
    assert [label for label, _, current in _links(html, "labs-risk-windows") if current] == ["3Y"]
    # A window is a display choice, not a new backtest: the period sent is the chart's.
    assert fake_portfolio.backtest_calls[-1][1] == "1Y"


def test_an_uncovered_comparison_window_says_why(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client, "?risk_window=10Y")

    assert len(_tables(html)) == 1  # the grid only
    assert f"Over 10Y: {LIMITED}." in _text(_section(html))


def test_an_unknown_risk_window_shows_1y_and_is_logged(client, caplog):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        html = _page(test_client, "?risk_window=2Y")

    assert [label for label, _, current in _links(html, "labs-risk-windows") if current] == ["1Y"]
    assert "unknown risk window '2Y'" in caplog.text


def test_risk_window_links_keep_the_mix_and_the_period(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    links = _links(_page(test_client, "?ticker=AAPL&weight=100&period=5Y"), "labs-risk-windows")

    assert [label for label, _, _ in links] == list(BACKTEST_RISK_WINDOWS)
    assert links[1][1] == (
        "/labs?ticker=AAPL&weight=100&period=5Y&risk_window=3Y#labs-risk-compare"
    )


def test_period_links_keep_the_risk_window(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    links = _links(_page(test_client, "?risk_window=3Y"), "labs-periods")

    assert links[0][1] == "/labs?risk_window=3Y&period=1M#labs-growth"


def test_the_mix_form_keeps_the_risk_window(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client, "?risk_window=5Y")

    assert '<input type="hidden" name="risk_window" value="5Y">' in html


def test_remove_keeps_the_risk_window(client):
    test_client, _ = client

    response = test_client.get(
        "/labs?ticker=AAPL&weight=50&ticker=IWDA.AS&weight=50&risk_window=3Y&remove=0",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("&risk_window=3Y")


# --- the drawdown chart ---------------------------------------------------------------------


def test_the_drawdown_chart_sits_under_the_growth_chart_with_its_island(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)
    html = _page(test_client)

    assert html.index('id="labs-chart"') < html.index('id="labs-drawdown-chart"')
    assert _drawdown_island(html) == {
        "period": "1Y",
        "dates": ["2025-10-09", "2025-10-10", "2025-10-13"],
        "values": [0.0, None, -0.0421],
    }
    assert re.search(r'<script src="/static/labs_drawdown_chart\.js\?v=\w+" defer></script>', html)


def test_no_drawdown_series_says_so(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio, _risk(drawdown=None))

    html = _page(test_client)

    assert _drawdown_island(html) is None
    assert "No drawdown for 1Y." in _text(html)


# --- glossary and logging -------------------------------------------------------------------


def test_every_risk_term_links_to_a_glossary_entry(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client)
    anchors = set(re.findall(r'<a class="term-link" href="/glossary#([\w-]+)">', html))
    glossary = test_client.get("/glossary").text

    assert {
        "cagr",
        "volatility",
        "max-drawdown",
        "sharpe",
        "sortino",
        "calmar",
        "returns-used",
        "drawdown",
    } <= anchors
    assert {a for a in anchors if f'<dt id="{a}"' not in glossary} == set()


def test_the_glossary_calls_every_risk_figure_historical(client):
    test_client, _ = client

    glossary = test_client.get("/glossary").text

    for anchor in ("cagr", "volatility", "max-drawdown", "sharpe", "sortino", "calmar"):
        entry = re.search(rf'<dt id="{anchor}">.*?</dd>', glossary, re.S).group(0)
        assert "historical" in entry.lower(), anchor
        assert "expected" not in entry.lower(), anchor


def test_the_risk_figures_are_logged(client, caplog):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        _page(test_client)

    assert (
        "labs: risk to 2026-10-09, windows from 2021-11-01 (limited by AAPL), "
        "3 of 5 covered, risk-free rate available, against VWCE.DE (available, "
        "no growth line: history starts after the growth period)." in caplog.text
    )


def test_missing_risk_free_rate_is_logged_with_its_reason(client, caplog):
    test_client, fake_portfolio = client
    risk_free = RiskFreeStatus(available=False, reason="no risk-free rate data", first_date=None)
    _with_risk(fake_portfolio, _risk(risk_free=risk_free))

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        _page(test_client)

    assert "no risk-free rate (no risk-free rate data), against" in caplog.text


# --- the benchmark (RK-14) -------------------------------------------------------------------

UNAVAILABLE = BacktestBenchmark(
    ticker="NOPE.XX",
    available=False,
    reason="not tracked",
    first_date=None,
    growth=None,
    growth_reason="benchmark unavailable: not tracked",
)


def _benchmark_form(html):
    match = re.search(r'<form[^>]*id="labs-benchmark-form".*?</form>', _section(html), re.S)
    assert match, "no benchmark form in the risk section"
    return match.group(0)


def _hidden(form):
    return re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form)


def test_the_default_benchmark_is_asked_for(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    form = _benchmark_form(_page(test_client))

    assert fake_portfolio.benchmarks == ["VWCE.DE"]
    assert 'name="benchmark"' in form and 'value="VWCE.DE"' in form


def test_benchmark_is_sent_and_kept_in_the_field(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    form = _benchmark_form(_page(test_client, "?benchmark=IWDA.AS"))

    assert fake_portfolio.benchmarks == ["IWDA.AS"]
    assert 'value="IWDA.AS"' in form
    assert 'action="/labs#labs-risk"' in form


def test_the_benchmark_form_keeps_the_rest_of_the_query(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    form = _benchmark_form(
        _page(test_client, "?ticker=AAPL&weight=100&period=5Y&risk_window=3Y&benchmark=IWDA.AS")
    )

    # Everything but the benchmark itself, which the field sends.
    assert _hidden(form) == [
        ("ticker", "AAPL"),
        ("weight", "100"),
        ("period", "5Y"),
        ("risk_window", "3Y"),
    ]


def test_the_benchmark_form_keeps_the_default_mix_default(client):
    # No ticker key: applying a benchmark mustn't turn the default mix into an edited one.
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    assert _hidden(_benchmark_form(_page(test_client))) == []


def test_the_benchmark_field_suggests_the_tracked_tickers(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    form = _benchmark_form(_page(test_client))

    datalist = re.search(r'<datalist id="labs-benchmark-options">(.*?)</datalist>', form, re.S)[1]
    assert re.findall(r'<option value="([^"]+)"', datalist) == ["IWDA.AS", "AAPL"]


def test_the_benchmark_field_suggests_each_ticker_with_its_name(client):
    """UX-2: the name is in the option; the value submitted is still the bare ticker."""
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)
    fake_portfolio.tracked_names = {"IWDA.AS": "iShares Core MSCI World"}

    form = _benchmark_form(_page(test_client))

    datalist = re.search(r'<datalist id="labs-benchmark-options">(.*?)</datalist>', form, re.S)[1]
    assert '<option value="IWDA.AS" label="IWDA.AS — iShares Core MSCI World">' in datalist
    assert '<option value="AAPL">' in datalist


def test_an_over_long_benchmark_says_so_and_sends_the_default(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    form = _benchmark_form(_page(test_client, "?benchmark=" + "A" * 21))

    assert fake_portfolio.benchmarks == ["VWCE.DE"]
    assert "at most 20 characters" in form


def test_period_and_window_links_keep_the_benchmark(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client, "?benchmark=IWDA.AS")

    assert "benchmark=IWDA.AS" in _links(html, "labs-periods")[0][1]
    assert "benchmark=IWDA.AS" in _links(html, "labs-risk-windows")[0][1]


def test_the_mix_form_keeps_the_benchmark(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client, "?benchmark=IWDA.AS")

    assert '<input type="hidden" name="benchmark" value="IWDA.AS">' in html


def test_remove_keeps_the_benchmark(client):
    test_client, _ = client

    response = test_client.get(
        "/labs?ticker=AAPL&weight=50&ticker=IWDA.AS&weight=50&benchmark=IWDA.AS&remove=0",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("&benchmark=IWDA.AS")


def _group_heading(table):
    return _text(re.search(r"<th[^>]*colspan.*?</th>", table, re.S).group(0))


def test_the_grid_has_the_relative_group_named_by_its_benchmark(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client)

    assert _group_heading(_grid(html)) == "Against VWCE.DE, history from 2019-07-29"
    beta = _grid_row(html, "Beta")
    assert "weekly" in beta
    # The first cell is the measure's own label.
    assert ">0.50<" in _cells(beta)[1]
    assert ">50.0%<" in _cells(_grid_row(html, "Tracking error"))[1]


def test_the_comparison_has_a_column_per_relative_measure(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    comparison = _comparison(_page(test_client))

    head = re.search(r"<thead.*?</thead>", comparison, re.S).group(0)
    for label in ("Beta", "Correlation", "Tracking error", "Information ratio"):
        assert f">{label}</a>" in head
    # Named once above the table, not under each of its four columns.
    assert "Beta to information ratio are against" in _text(_section(_page(test_client)))
    first_row = re.findall(r"<tbody.*?</tbody>", comparison, re.S)[0]
    # Asset, six measures, four relative, returns used.
    assert len(_cells(re.findall(r"<tr role=\"row\".*?</tr>", first_row, re.S)[0])) == 12


def test_an_unavailable_benchmark_says_why_once(client):
    test_client, fake_portfolio = client
    reason = "benchmark unavailable: not tracked"
    greyed = {key: _figure(None, frequency="weekly", reason=reason) for key in RELATIVE_FIGURE_KEYS}
    rows = {w: _covered(relative=greyed) for w in ("1Y", "3Y")}
    _with_risk(
        fake_portfolio,
        _risk(
            mix=_risk_row(None, **rows),
            assets=(_risk_row("IWDA.AS", **rows), _risk_row("AAPL", **rows)),
            benchmark=UNAVAILABLE,
        ),
    )

    html = _page(test_client, "?benchmark=NOPE.XX")

    assert _group_heading(_grid(html)) == "Against NOPE.XX: unavailable, not tracked"
    assert reason not in _section(html)
    assert "Unavailable" in _cells(_grid_row(html, "Beta"))[1]


def _series_box(html):
    return re.search(r'<fieldset class="labs-series".*?</fieldset>', html, re.S).group(0)


def _growth_island(html):
    match = re.search(r'<script id="labs-chart-data" type="application/json">(.*?)</script>', html)
    return json.loads(match.group(1))


def test_the_benchmark_line_is_on_by_default_with_its_own_box(client):
    test_client, fake_portfolio = client
    line = (Decimal("0"), Decimal("0.004"), Decimal("-0.01"))
    _with_risk(fake_portfolio, _risk(benchmark=replace(BENCHMARK, growth=line, growth_reason=None)))

    html = _page(test_client)

    box = re.search(
        r'<label[^>]*>\s*<input[^>]*data-series="__benchmark"[^>]*>.*?</label>',
        _series_box(html),
        re.S,
    ).group(0)
    assert "checked" in box and "disabled" not in box
    assert "VWCE.DE" in box and "benchmark" in box
    assert _growth_island(html)["benchmark"] == {
        "ticker": "VWCE.DE",
        "name": None,
        "values": [0.0, 0.004, -0.01],
        "reason": None,
    }
    assert "the dashed line is the benchmark" in _series_box(html)


def test_the_benchmark_line_and_its_box_are_named(client):
    """UX-3: the island names the benchmark for the tooltip, and its label hosts the name."""
    test_client, fake_portfolio = client
    line = (Decimal("0"), Decimal("0.004"), Decimal("-0.01"))
    _with_risk(fake_portfolio, _risk(benchmark=replace(BENCHMARK, growth=line, growth_reason=None)))
    fake_portfolio.tracked_names = {"VWCE.DE": "Vanguard FTSE All-World"}

    html = _page(test_client)

    box = re.search(
        r'<label[^>]*>\s*<input[^>]*data-series="__benchmark"[^>]*>.*?</label>',
        _series_box(html),
        re.S,
    ).group(0)
    assert '<span class="ticker"><span class="mono">VWCE.DE</span>' in box
    assert "Vanguard FTSE All-World" in box and "(benchmark)" in box
    assert _growth_island(html)["benchmark"]["name"] == "Vanguard FTSE All-World"


def test_no_benchmark_line_disables_its_box_and_says_why(client):
    test_client, fake_portfolio = client
    reason = "benchmark history too short (from 2019-07-29)"
    _with_risk(fake_portfolio, _risk(benchmark=replace(BENCHMARK, growth_reason=reason)))

    html = _page(test_client)

    box = re.search(
        r'<label[^>]*>\s*<input[^>]*data-series="__benchmark"[^>]*>.*?</label>',
        _series_box(html),
        re.S,
    ).group(0)
    assert "disabled" in box and "checked" not in box
    assert "No benchmark line: benchmark history too short (from 2019-07-29)." in _text(html)


# --- the results order and the risk fold (UX-8) -----------------------------------------------


def _fold(html):
    # The shared fold look (UX-7), named for its section.
    match = re.search(r'<details class="fold labs-risk-fold"[^>]*>', _section(html))
    assert match, "no risk fold"
    return match.group(0)


def test_results_run_returns_growth_drawdown_then_risk(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client)

    order = [
        html.index("<table"),
        html.index('<section class="labs-growth"'),
        html.index('class="labs-drawdown"'),
        html.index('<section class="labs-risk"'),
    ]
    assert order == sorted(order)


def test_the_risk_figures_are_folded_under_their_heading(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    section = _section(_page(test_client))

    assert section.index('<h2 id="labs-risk-title">Risk</h2>') < section.index("<details")
    summary = _text(re.search(r"<summary[^>]*>(.*?)</summary>", section, re.S).group(1))
    assert summary == "By window and per asset, against VWCE.DE"
    # Every table and the benchmark form are inside the fold.
    inside = section.split("<details", 1)[1]
    assert inside.count("<table") == 2
    assert 'class="benchmark-form' in inside or "benchmark-form" in inside


def test_the_risk_fold_is_closed_by_default(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    assert " open" not in _fold(_page(test_client))


def test_the_risk_fold_stays_closed_for_the_mix_forms_default_fields(client):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    html = _page(test_client, "?period=3M&risk_window=1Y&benchmark=VWCE.DE")

    assert " open" not in _fold(html)


@pytest.mark.parametrize(
    "query",
    ["?risk_window=3Y", "?benchmark=IWDA.AS", "?benchmark=" + "A" * 21],
    ids=["window", "benchmark", "benchmark-error"],
)
def test_the_risk_fold_opens_for_a_chosen_window_benchmark_or_error(client, query):
    test_client, fake_portfolio = client
    _with_risk(fake_portfolio)

    assert " open" in _fold(_page(test_client, query))
