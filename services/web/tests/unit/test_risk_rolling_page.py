"""GET /risk's rolling charts (RK-25): ex-post's and ex-ante's rolling 1Y volatility and Sharpe,
and ex-ante's rolling correlation of a pair -- the pair web asks portfolio for, the picker that
changes it, what each section renders, and the empty, gap and failure states.

Like test_risk_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import json
import logging
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from html import unescape

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    RELATIVE_FIGURE_KEYS,
    RISK_FIGURE_KEYS,
    RISK_WINDOWS,
    BacktestAssetStatus,
    BenchmarkStatus,
    ExAnteRisk,
    ExPostRisk,
    ExPostWindow,
    Holding,
    Holdings,
    LookThrough,
    RiskFigure,
    RollingCorrelation,
    RollingRisk,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
END = date(2026, 10, 16)
BENCHMARK = BenchmarkStatus(
    ticker="VWCE.DE", available=True, reason=None, first_date=date(2019, 7, 29)
)
GAP = "insufficient history (have 51, need 52)"
MONTH_ENDS = (date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30), END)


def _figure(value, end=END, *, standard_error=None, reason=None) -> RiskFigure:
    return RiskFigure(
        value=value,
        window_start=end.replace(year=end.year - 1),
        window_end=end,
        frequency="weekly",
        q=52.0,
        n_obs=52 if value is not None else 51,
        n_gaps=0,
        flags=(),
        reason=None if value is not None else reason,
        standard_error=standard_error,
    )


def _rolling(volatility=(0.1411, None, 0.1522, 0.1633, 0.1744), sharpe=None) -> RollingRisk:
    sharpe = sharpe or (0.81, None, 0.92, -0.13, 1.04)
    return RollingRisk(
        window="1Y",
        step="monthly",
        volatility=tuple(_figure(v, end, reason=GAP) for v, end in zip(volatility, MONTH_ENDS)),
        sharpe=tuple(
            _figure(v, end, standard_error=0.6, reason=GAP) for v, end in zip(sharpe, MONTH_ENDS)
        ),
    )


def _pair(a="IWDA.AS", b="portfolio", values=(0.9123, None, 0.9456), reason=None):
    ends = (date(2026, 10, 2), date(2026, 10, 9), END)
    points = () if reason else tuple(_figure(v, e, reason=GAP) for v, e in zip(values, ends))
    return RollingCorrelation(a=a, b=b, window="52W", step="weekly", reason=reason, points=points)


def _window(label) -> ExPostWindow:
    return ExPostWindow(
        label=label,
        start=date(2025, 10, 16),
        end=END,
        covered=True,
        figures={name: _figure(0.1) for name in RISK_FIGURE_KEYS},
        peak_date=None,
        trough_date=None,
        recovery_date=None,
        max_days_under_water=None,
        relative={name: _figure(0.1) for name in RELATIVE_FIGURE_KEYS},
    )


def _ex_post(**changes) -> ExPostRisk:
    risk = ExPostRisk(
        label="ex-post · invested assets",
        base_date=date(2023, 1, 2),
        end_date=END,
        rf_available=True,
        benchmark=BENCHMARK,
        windows=tuple(_window(label) for label in RISK_WINDOWS),
        excluded=(),
        rolling=_rolling(),
    )
    return replace(risk, **changes)


def _ex_ante(**changes) -> ExAnteRisk:
    # Nothing but statuses and the rolling figures: this file is about the rolling block only.
    risk = ExAnteRisk(
        label="ex-ante · constant weights",
        currency="eur",
        rf_available=True,
        window=None,
        benchmark=BENCHMARK,
        statuses=tuple(BacktestAssetStatus(t, "included", None) for t in ("IWDA.AS", "AAPL")),
        sigma_p=None,
        diversification_ratio=None,
        effective_n=None,
        holdings=None,
        constant_weight=None,
        correlation=None,
        rolling=_rolling(volatility=(0.2011, 0.2122, 0.2233, None, 0.2455)),
        rolling_correlation=_pair(),
    )
    return replace(risk, **changes)


def _held(ticker, market_value) -> Holding:
    return Holding(
        canonical_ticker=ticker,
        quantity=Decimal("1"),
        average_cost_eur=None,
        cost_basis_eur=Decimal("1"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=None,
        market_value_native=None,
        priced=True,
        price_date=END,
        market_value_eur=Decimal(market_value),
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.ex_post: ExPostRisk = _ex_post()
        self.ex_post_error: Exception | None = None
        # 70/30 by market value: IWDA.AS is the heaviest.
        self.holdings = Holdings(
            holdings=(_held("AAPL", "3000"), _held("IWDA.AS", "7000")), totals=None
        )
        self.ex_ante: ExAnteRisk = _ex_ante()
        self.ex_ante_error: Exception | None = None
        self.pairs: list = []
        self.excludes: list = []

    async def list_tickers(self):
        return ["IWDA.AS", "VWCE.DE"]

    async def ex_post_risk(self, *, user_id, benchmark):
        if self.ex_post_error:
            raise self.ex_post_error
        return self.ex_post

    async def list_holdings(self, *, user_id):
        return self.holdings

    async def ex_ante_risk(self, *, assets, window, benchmark, exclude, rolling_pair=None):
        self.pairs.append(rolling_pair)
        self.excludes.append(tuple(exclude))
        if self.ex_ante_error:
            raise self.ex_ante_error
        return self.ex_ante

    async def look_through(self, *, assets):
        return LookThrough(assets=(), exposures=None)


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
    user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
    fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
    with TestClient(app) as test_client:
        test_client.cookies.set("stakehold_session", "tok")
        yield test_client, fake_portfolio


def _section(html, section_id) -> str:
    """One <section>'s HTML, so a test can tell ex-post's charts from ex-ante's."""
    start = html.index(f'aria-labelledby="{section_id}-title"')
    end = html.find("<section", start)
    return html[start : end if end != -1 else len(html)]


def _island(html, data_id) -> dict:
    match = re.search(rf'<script id="{data_id}" type="application/json">(.*?)</script>', html)
    assert match, f"no data island {data_id}"
    return json.loads(match.group(1))


# --- the pair web asks for ---


def test_the_default_pair_is_the_heaviest_holding_against_the_portfolio(client):
    test_client, fake = client

    test_client.get("/risk")

    assert fake.pairs == [("IWDA.AS", None)]


def test_a_requested_pair_is_sent(client):
    test_client, fake = client

    test_client.get("/risk?corr_a=AAPL&corr_b=VWCE.DE")

    assert fake.pairs == [("AAPL", "VWCE.DE")]


def test_a_stale_pair_falls_back_to_the_default_and_says_so(client, caplog):
    # AAPL is excluded, so portfolio would reject the whole request with a 422.
    test_client, fake = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        response = test_client.get("/risk?exclude=AAPL&corr_a=AAPL")

    assert response.status_code == 200
    assert fake.pairs == [("IWDA.AS", None)]
    assert "AAPL vs the portfolio isn't a pair from the measured mix" in unescape(response.text)
    assert "rolling pair dropped (AAPL vs the portfolio" in caplog.text


# --- what each section renders ---


def test_ex_post_draws_rolling_volatility_and_sharpe(client):
    test_client, _ = client

    html = _section(test_client.get("/risk").text, "risk-ex-post")

    assert 'id="risk-ex-post-rolling-volatility"' in html
    assert 'id="risk-ex-post-rolling-sharpe"' in html
    volatility = _island(html, "risk-ex-post-rolling-volatility-data")
    assert volatility["values"] == [0.1411, None, 0.1522, 0.1633, 0.1744]
    assert volatility["dates"][-1] == "2026-10-16"
    sharpe = _island(html, "risk-ex-post-rolling-sharpe-data")
    assert sharpe["upper"][0] == pytest.approx(1.41)


def test_ex_post_rolling_has_a_summary_and_its_gap_note(client):
    test_client, _ = client

    html = _section(test_client.get("/risk").text, "risk-ex-post")

    assert "17.4%" in html  # the latest volatility and the highest
    assert "14.1%" in html  # the lowest
    assert "1.04 ± 0.60" in html  # the latest Sharpe with its 1 s.e.
    assert "The shaded band around the Sharpe is ± 1 s.e." in html
    assert f'No point for the window ending <span class="mono">2026-07-31</span>: {GAP}' in html


def test_ex_ante_draws_its_own_rolling_charts_and_the_pair(client):
    test_client, _ = client

    html = _section(test_client.get("/risk").text, "risk-ex-ante")

    assert _island(html, "risk-ex-ante-rolling-volatility-data")["values"][0] == 0.2011
    correlation = _island(html, "risk-ex-ante-rolling-correlation-data")
    assert correlation["kind"] == "rho"
    assert correlation["values"] == [0.9123, None, 0.9456]
    assert "IWDA.AS</span> against the portfolio" in html


def test_the_chart_scripts_load_once(client):
    test_client, _ = client

    html = test_client.get("/risk").text

    for script in ("vendor/chart.min.js", "chart_theme.js", "risk_rolling_chart.js"):
        assert len(re.findall(rf'<script src="/static/{script}\?v=\w+" defer></script>', html)) == 1


def test_the_picker_marks_the_pair_and_resends_the_rest_of_the_query(client):
    test_client, _ = client

    html = test_client.get("/risk?benchmark=VWCE.DE&ex_ante_window=5Y&corr_a=AAPL").text
    picker = html[html.index('id="risk-rolling-pair"') :]
    picker = picker[: picker.index("</form>")]

    assert '<input type="hidden" name="benchmark" value="VWCE.DE">' in picker
    assert '<input type="hidden" name="ex_ante_window" value="5Y">' in picker
    assert 'name="corr_a"' in picker and 'name="corr_b"' in picker
    assert '<option value="AAPL" selected>' in picker
    assert '<option value="portfolio" selected>the portfolio</option>' in picker
    assert picker.count('type="hidden" name="corr_') == 0


def test_the_benchmark_form_keeps_the_pair(client):
    test_client, _ = client

    html = test_client.get("/risk?corr_a=AAPL&corr_b=portfolio").text
    form = html[html.index('id="risk-benchmark-form"') :]
    form = form[: form.index("</form>")]

    assert '<input type="hidden" name="corr_a" value="AAPL">' in form
    assert '<input type="hidden" name="corr_b" value="portfolio">' in form


# --- empty, greyed and failure states ---


def test_no_point_yet_says_the_history_is_under_a_year(client):
    test_client, fake = client
    fake.ex_post = _ex_post(rolling=RollingRisk("1Y", "monthly", (), ()))

    html = _section(test_client.get("/risk").text, "risk-ex-post")

    assert "No rolling point yet: the history is under a year." in html
    assert "risk-ex-post-rolling-volatility" not in html


def test_a_sharpe_greyed_throughout_shows_its_reason_and_no_chart(client):
    test_client, fake = client
    rolling = _rolling()
    greyed = tuple(replace(f, value=None, reason="no risk-free rate data") for f in rolling.sharpe)
    fake.ex_post = _ex_post(rolling=replace(rolling, sharpe=greyed))

    html = _section(test_client.get("/risk").text, "risk-ex-post")

    assert 'id="risk-ex-post-rolling-volatility"' in html
    assert 'id="risk-ex-post-rolling-sharpe"' not in html
    assert "no risk-free rate data" in html
    assert "shaded band" not in html  # no Sharpe chart, so no band to explain


def test_a_pair_portfolio_couldnt_measure_shows_its_reason(client):
    test_client, fake = client
    fake.ex_ante = _ex_ante(rolling_correlation=_pair(reason="IWDA.AS left out: no history"))

    html = _section(test_client.get("/risk").text, "risk-ex-ante")

    assert "IWDA.AS left out: no history" in html
    assert "risk-ex-ante-rolling-correlation-data" not in html
    assert 'id="risk-rolling-pair"' in html  # the picker is the way out


def test_a_failed_ex_ante_call_leaves_ex_post_rolling_in_place(client):
    test_client, fake = client
    fake.ex_ante_error = PortfolioUnavailable("down")

    html = test_client.get("/risk").text

    assert 'id="risk-ex-post-rolling-volatility"' in html
    assert "risk-ex-ante-rolling" not in html


def test_a_failed_ex_post_call_leaves_ex_ante_rolling_in_place(client):
    test_client, fake = client
    fake.ex_post_error = PortfolioUnavailable("down")

    html = test_client.get("/risk").text

    assert "risk-ex-post-rolling" not in html
    assert 'id="risk-ex-ante-rolling-volatility"' in html


def test_the_rolling_glossary_links_land_on_entries(client):
    test_client, _ = client

    html = test_client.get("/risk").text
    glossary = test_client.get("/glossary").text

    for anchor in ("rolling-window", "rolling-correlation"):
        assert f'href="/glossary#{anchor}"' in html
        assert f'id="{anchor}"' in glossary


# --- logging ---


def test_the_rolling_outcomes_are_logged(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        test_client.get("/risk")

    assert (
        "risk: ex-post rolling 1Y: 5 monthly point(s), 1 volatility greyed, 1 sharpe greyed."
        in (caplog.text)
    )
    assert "rolling pair IWDA.AS vs the portfolio (default)" in caplog.text
    assert "risk: rolling correlation IWDA.AS vs the portfolio: 3 weekly point(s), 1 greyed." in (
        caplog.text
    )
