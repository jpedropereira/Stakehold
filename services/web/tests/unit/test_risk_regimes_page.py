"""GET /risk's regime tables (RK-28): ex-post's on the TWR and ex-ante's on today's mix, each
labelled, grouped by dimension, with partial and ongoing regimes, the short-regime flag, a
greyed benchmark, and the empty and failure states.

Like test_risk_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import logging
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from html import unescape
from types import SimpleNamespace

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
    RegimeFigures,
    RegimeRow,
    RegimeView,
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


def _daily(value, start, end, *, flags=(), reason=None) -> RiskFigure:
    return RiskFigure(
        value=value,
        window_start=start,
        window_end=end,
        frequency="daily",
        q=252.0,
        n_obs=33,
        n_gaps=0,
        flags=tuple(flags),
        reason=None if value is not None else reason,
        standard_error=None,
    )


def _regime(
    regime_id,
    dimension,
    name,
    start,
    end,
    *,
    measured_start=None,
    values=None,
    benchmark_reason=None,
):
    m_start, m_end = measured_start or start, end or END
    cr, mdd, b_cr, b_mdd = values or (-0.1234, -0.1456, -0.1111, -0.1622)

    def figures(cumulative, drawdown, reason=None):
        return RegimeFigures(
            _daily(cumulative, m_start, m_end, reason=reason),
            _daily(drawdown, m_start, m_end, flags=("short_regime",), reason=reason),
        )

    return RegimeRow(
        regime_id=regime_id,
        dimension=dimension,
        scope="SPX",
        name=name,
        severity="severe",
        source="manual",
        start_date=start,
        end_date=end,
        measured_start=m_start,
        measured_end=m_end,
        partial=m_start != start,
        ongoing=end is None,
        portfolio=figures(cr, mdd),
        benchmark=figures(b_cr, b_mdd, reason=benchmark_reason),
    )


def _regimes(rows=None, **changes) -> RegimeView:
    rows = (
        rows
        if rows is not None
        else (
            _regime(
                "spx_corr_2025",
                "drawdown",
                "2025 tariff selloff",
                date(2025, 2, 19),
                date(2025, 4, 8),
            ),
            _regime(
                "ukraine_war",
                "event",
                "Russian invasion of Ukraine",
                date(2022, 2, 24),
                None,
                measured_start=date(2023, 1, 2),
                values=(0.3012, -0.1203, 0.2501, -0.1508),
            ),
        )
    )
    return replace(RegimeView(True, None, 94, rows), **changes)


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
        regimes=_regimes(),
    )
    return replace(risk, **changes)


def _ex_ante(**changes) -> ExAnteRisk:
    # Nothing but statuses and the regimes: this file is about the regime tables only.
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
        fx_decomposition=None,
        rolling=_rolling(volatility=(0.2011, 0.2122, 0.2233, None, 0.2455)),
        rolling_correlation=_pair(),
        regimes=_regimes(
            rows=(
                _regime(
                    "gfc",
                    "event",
                    "Global financial crisis",
                    date(2007, 8, 9),
                    date(2009, 3, 9),
                    values=(-0.4012, -0.5203, None, None),
                    benchmark_reason="benchmark history too short (from 2019-07-29)",
                ),
            )
        ),
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


def _block(html, block_id) -> str:
    start = html.index(f'id="{block_id}"')
    end = html.find('class="risk-regimes"', start + 1)
    section_end = html.find("</section>", start)
    return html[start : min(e for e in (end, section_end) if e != -1)]


# --- what each section shows ---


def test_ex_post_shows_its_regimes_grouped_by_dimension(client):
    test_client, _ = client

    html = test_client.get("/risk").text

    block = unescape(_block(html, "risk-ex-post-regimes"))
    assert "Market regimes" in block
    assert block.index("Drawdowns") < block.index("2025 tariff selloff") < block.index("Events")
    assert "Russian invasion of Ukraine" in block
    for value in ("−12.3%", "−14.6%", "−11.1%", "−16.2%", "+30.1%"):
        assert value in block
    assert "VWCE.DE" in block


def test_ex_ante_shows_its_own_regimes_apart_from_ex_post(client):
    test_client, _ = client

    ex_post = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))
    ex_ante = unescape(_block(test_client.get("/risk/ex-ante").text, "risk-ex-ante-regimes"))
    assert "Global financial crisis" in ex_ante and "Global financial crisis" not in ex_post
    assert "2025 tariff selloff" not in ex_ante
    assert "today's mix" in ex_ante
    assert "benchmark history too short (from 2019-07-29)" in ex_ante


def test_a_partial_ongoing_regime_says_where_it_was_measured(client):
    test_client, _ = client

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    assert "ongoing" in block
    assert "measured from" in block and "2023-01-02" in block


def test_a_short_regime_drawdown_has_a_marker_and_its_note(client):
    test_client, _ = client

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    assert '<sup class="risk-flag">1</sup>' in block
    assert "Fewer than 200 trading days" in block


def test_each_figure_title_names_its_regime_window_and_label(client):
    test_client, _ = client

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    assert (
        'title="2025 tariff selloff: 2025-02-19 to 2025-04-08 · daily · 33 obs · 0 gaps · '
        'ex-post · invested assets"' in block
    )


def test_copy_says_historical_never_expected(client):
    test_client, _ = client

    for path, block_id in (
        ("/risk", "risk-ex-post-regimes"),
        ("/risk/ex-ante", "risk-ex-ante-regimes"),
    ):
        block = unescape(_block(test_client.get(path).text, block_id)).lower()
        assert "historical" in block
        assert "expected" not in block


def test_an_unavailable_benchmark_says_why_once(client):
    test_client, fake = client
    reason = "benchmark unavailable: not tracked"
    rows = (
        _regime(
            "spx_corr_2025",
            "drawdown",
            "2025 tariff selloff",
            date(2025, 2, 19),
            date(2025, 4, 8),
            values=(-0.1, -0.12, None, None),
            benchmark_reason=reason,
        ),
    )
    fake.ex_post = _ex_post(
        benchmark=BenchmarkStatus("NOPE", False, "not tracked", None), regimes=_regimes(rows)
    )

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    assert block.count("not tracked") == 1
    assert block.count("Unavailable") == 2


@pytest.mark.parametrize(
    "view, text",
    [
        (
            RegimeView(False, "no regimes loaded", 0, ()),
            "No market regimes are loaded yet, so there is nothing to compare.",
        ),
        (
            RegimeView(False, "regimes unavailable: data-store call failed", None, ()),
            "Couldn't load the market regimes. Try again later.",
        ),
        (RegimeView(True, None, 94, ()), "No market regime overlaps this history."),
    ],
)
def test_a_view_without_rows_says_why(client, view, text):
    test_client, fake = client
    fake.ex_post = _ex_post(regimes=view)

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    assert text in block
    assert "<table" not in block
    assert "<details" not in block  # one line: nothing to fold (UX-7)


def test_ex_ante_without_a_regime_view_has_no_block(client):
    test_client, fake = client
    fake.ex_ante = _ex_ante(regimes=None)

    assert 'id="risk-ex-ante-regimes"' not in test_client.get("/risk/ex-ante").text
    assert 'id="risk-ex-post-regimes"' in test_client.get("/risk").text


def test_a_failed_ex_post_load_takes_only_its_own_regimes(client):
    test_client, fake = client
    fake.ex_post_error = PortfolioUnavailable("down")

    assert 'id="risk-ex-post-regimes"' not in test_client.get("/risk").text
    assert 'id="risk-ex-ante-regimes"' in test_client.get("/risk/ex-ante").text


def test_each_section_logs_its_regimes(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        test_client.get("/risk")
        test_client.get("/risk/ex-ante")

    assert "risk: ex-post regimes: 2 shown in 2 group(s), benchmark greyed on 0." in caplog.text
    assert "risk: ex-ante regimes: 1 shown in 1 group(s), benchmark greyed on 1." in caplog.text


def test_a_view_without_rows_is_logged_with_its_reason(client, caplog):
    test_client, fake = client
    fake.ex_post = _ex_post(regimes=RegimeView(False, "no regimes loaded", 0, ()))

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        test_client.get("/risk")

    assert "risk: ex-post regimes: none shown, no regimes loaded." in caplog.text


def test_each_dimension_is_a_closed_group_naming_its_count(client):
    test_client, fake = client
    rows = (
        _regime("a", "drawdown", "Crash A", date(2024, 2, 5), date(2024, 3, 1)),
        _regime("b", "drawdown", "Crash B", date(2025, 2, 19), date(2025, 4, 8)),
        _regime("c", "event", "Shock C", date(2024, 8, 5), date(2024, 8, 9)),
    )
    fake.ex_post = _ex_post(regimes=_regimes(rows))

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    assert block.count('<details class="fold risk-regime-group">') == 2
    assert " open" not in block
    assert 'Drawdowns · <span class="mono">2</span> regimes</summary>' in block
    assert 'Events · <span class="mono">1</span> regime</summary>' in block


def test_the_regimes_fold_under_their_heading_with_their_totals(client):
    """UX-7: one outer fold, closed, holding the note, the groups and the legend."""
    test_client, fake = client
    rows = (
        _regime("a", "drawdown", "Crash A", date(2024, 2, 5), date(2024, 3, 1)),
        _regime("b", "drawdown", "Crash B", date(2025, 2, 19), date(2025, 4, 8)),
        _regime("c", "event", "Shock C", date(2024, 8, 5), date(2024, 8, 9)),
    )
    fake.ex_post = _ex_post(regimes=_regimes(rows))

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    heading = block.index("<h3>Market regimes</h3>")
    outer = block.index('<details class="fold">')
    assert heading < outer
    assert '<summary><span class="mono">3</span> regimes in 2 groups</summary>' in block
    assert (
        outer
        < block.index("historically, during each market")
        < block.index('class="fold risk-regime-group"')
    )
    # The legend comes after the groups and closes the outer fold, so the groups stay nested.
    assert block.rindex('class="fold risk-regime-group"') < block.index('class="risk-legend"')
    assert block.index('class="risk-legend"') < block.rindex("</details>")


def test_one_regime_in_one_group_reads_in_the_singular(client):
    test_client, fake = client
    rows = (_regime("c", "event", "Shock C", date(2024, 8, 5), date(2024, 8, 9)),)
    fake.ex_post = _ex_post(regimes=_regimes(rows))

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    assert '<summary><span class="mono">1</span> regime in 1 group</summary>' in block


# --- ticker names (UX-2) ---


def test_the_regime_tables_name_the_benchmark(client):
    test_client, fake = client
    fake.tracked_names = {"VWCE.DE": "Vanguard FTSE All-World"}

    block = _block(test_client.get("/risk").text, "risk-ex-post-regimes")

    note = re.search(r'<p class="labs-note">How .*?</p>', block, re.S).group(0)
    assert '<span class="ticker" tabindex="0"><span class="mono">VWCE.DE</span>' in note
    assert "Vanguard FTSE All-World" in note
    header = re.search(r'<th class="num"[^>]*>(?:(?!</th>).)*VWCE\.DE.*?</th>', block, re.S)
    assert "Vanguard FTSE All-World" in header.group(0)
    # The table's accessible name stays plain text: markup can't go in an attribute.
    assert re.search(r'aria-label="[^"]*: your holdings and VWCE\.DE"', block)


# --- copy (UX-9) ---------------------------------------------------------------------------------


def test_the_regime_note_is_one_sentence_and_leaves_the_method_to_the_glossary(client):
    test_client, _ = client

    block = unescape(_block(test_client.get("/risk").text, "risk-ex-post-regimes"))

    note = re.search(r'<p class="labs-note">How .*?</p>', block, re.S).group(0)
    text = " ".join(re.sub(r"<[^>]+>", " ", note).split())
    # The method sentence, then at most the benchmark's history, which is data.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s]
    assert len(sentences) <= 2, sentences
    assert not any("history starts" not in s for s in sentences[1:]), sentences
    assert 'href="/glossary#regime"' in note
    assert "Open a group" not in block
    assert "began before this history" not in block
