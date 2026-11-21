"""GET /risk's what-if section (RK-20): the GET form (ticker, weight %, funding), what it sends
portfolio's POST /risk/what-if, its inline errors, the before/after table, the threshold, the
survivorship warning, the capped and shortened-window notes, and the empty and error states.
The window and exclusions are the ex-ante section's, and every form keeps the others' fields.

Like test_risk_page.py, it carries its own minimal fakes. The ex-post and ex-ante answers are
"nothing to measure", so only this section has content.
"""

import html
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
    BacktestAssetStatus,
    BenchmarkStatus,
    CandidateRanking,
    ExAnteRisk,
    ExPostRisk,
    Holding,
    Holdings,
    LookThrough,
    RankingWindow,
    RiskFigure,
    WhatIf,
    WhatIfCandidate,
    WhatIfFunding,
    WhatIfRow,
    WhatIfThreshold,
    WhatIfWindow,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
LABEL = "ex-ante · constant weights"
END = date(2026, 10, 16)
START = date(2023, 10, 20)
BENCHMARK = BenchmarkStatus(
    ticker="VWCE.DE", available=False, reason="not fetched", first_date=None
)
R14 = (
    "Historical figures come from today's index members and overstate what an investor could "
    "have achieved."
)


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


# 70/20/10 by market value: build_default_mix sends these weights, largest first.
_HOLDINGS = (_held("IWDA.AS", "7000"), _held("TSLA", "1000"), _held("AAPL", "2000"))
_MIX = (("IWDA.AS", Decimal("0.7")), ("AAPL", Decimal("0.2")), ("TSLA", Decimal("0.1")))


def _figure(value, frequency="weekly", flags=(), reason=None, standard_error=None):
    return RiskFigure(
        value=value,
        window_start=START,
        window_end=END,
        frequency=frequency,
        q=52.0 if frequency == "weekly" else 251.4,
        n_obs=156 if frequency == "weekly" else 753,
        n_gaps=0,
        flags=tuple(flags),
        reason=reason,
        standard_error=standard_error,
    )


def _row(before, after, delta, delta_se=None, frequency="weekly", flags=()):
    return WhatIfRow(
        before=None if before is None else _figure(before, frequency),
        after=_figure(after, frequency, flags),
        delta=delta,
        delta_se=delta_se,
    )


def _rows(flags=()):
    return {
        "sigma_p": _row(0.1412, 0.1391, -0.0021, flags=flags),
        "diversification_ratio": _row(1.16, 1.25, 0.09, flags=flags),
        "candidate_pcr": _row(None, 0.0312, None, flags=flags),
        "candidate_correlation": _row(None, 0.1234, None, flags=flags),
        "sharpe": _row(0.8012, 0.8811, 0.0799, delta_se=0.1012, flags=flags),
        "max_drawdown": _row(-0.2345, -0.2212, 0.0133, frequency="daily", flags=flags),
    }


_WINDOW = WhatIfWindow(
    label="3Y",
    requested_start=date(2023, 10, 16),
    start=START,
    end=END,
    limited_by=(),
    shortened=False,
    start_without_candidate=START,
    end_without_candidate=END,
)
_CANDIDATE = WhatIfCandidate(
    ticker="EGLN.L",
    status="included",
    reason=None,
    requested_weight=0.1,
    weight=0.1,
    capped=False,
    survivorship=False,
)
_THRESHOLD = WhatIfThreshold(
    applicable=True,
    reason=None,
    candidate_sharpe=_figure(0.5123, standard_error=0.58),
    correlation=0.1234,
    portfolio_sharpe=0.8012,
    threshold=0.0989,
    improves=True,
)


def _what_if(**changes) -> WhatIf:
    what_if = WhatIf(
        label=LABEL,
        rf_available=True,
        statuses=tuple(BacktestAssetStatus(t, "included", None) for t, _ in _MIX),
        candidate=_CANDIDATE,
        funding=WhatIfFunding("pro_rata", None, None),
        window=_WINDOW,
        rows=_rows(),
        threshold=_THRESHOLD,
    )
    return replace(what_if, **changes)


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.tickers = ["EGLN.L", "IWDA.AS"]
        self.holdings = Holdings(holdings=_HOLDINGS, totals=None)
        self.holdings_error: Exception | None = None
        self.ex_ante_error: Exception | None = None
        self.what_if_result: WhatIf = _what_if()
        self.what_if_error: Exception | None = None
        self.ranking_result = None

    async def list_tickers(self):
        return self.tickers

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
        return ExPostRisk("ex-post · invested assets", None, None, False, BENCHMARK, (), ())

    async def list_holdings(self, *, user_id):
        self.calls.append(("list_holdings", user_id))
        if self.holdings_error:
            raise self.holdings_error
        return self.holdings

    async def ex_ante_risk(self, *, assets, window, benchmark, exclude, rolling_pair=None):
        self.calls.append(("ex_ante_risk", tuple(assets), window, tuple(exclude)))
        if self.ex_ante_error:
            raise self.ex_ante_error
        statuses = tuple(BacktestAssetStatus(t, "excluded", "excluded") for t, _ in assets)
        return ExAnteRisk(LABEL, "eur", False, None, BENCHMARK, statuses, *(None,) * 7)

    async def look_through(self, *, assets):
        return LookThrough(assets=(), exposures=None)

    async def what_if(self, *, assets, candidate, funding, window, exclude):
        self.calls.append(("what_if", tuple(assets), candidate, funding, window, tuple(exclude)))
        if self.what_if_error:
            raise self.what_if_error
        return self.what_if_result

    async def rank_candidates(self, *, assets, weight, window, exclude, rank_by):
        # The ranking's own tests are test_risk_ranking_page.py.
        if self.ranking_result is None:
            raise PortfolioUnavailable("not under test here")
        return self.ranking_result


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


def _section(page: str) -> str:
    match = re.search(
        r'<section class="risk-section" aria-labelledby="risk-what-if-title".*?</section>',
        page,
        re.S,
    )
    assert match, "no what-if section"
    return match.group(0)


def _get(test_client, query=""):
    response = test_client.get(f"/risk/what-if{query}")
    assert response.status_code == 200
    return _section(response.text)


def _what_if_calls(fake):
    return [call for call in fake.calls if call[0] == "what_if"]


def _text(section: str) -> str:
    """The section's words, tags dropped and whitespace collapsed, with no space left before
    punctuation that followed a tag."""
    words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", section)).split())
    return re.sub(r"\( ", "(", re.sub(r" ([.,:)']|'s\b)", r"\1", words))


def _hidden(form: str) -> list[tuple[str, str]]:
    return re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form)


def _form(section: str, form_id: str) -> str:
    match = re.search(rf'<form[^>]*id="{form_id}".*?</form>', section, re.S)
    assert match, f"no form {form_id}"
    return match.group(0)


WHAT_IF_QUERY = "?candidate=EGLN.L&candidate_weight=10&funding=pro_rata"


# --- the form ---------------------------------------------------------------------------


def test_no_candidate_shows_the_empty_form_and_calls_nothing(client):
    test_client, fake = client

    section = _get(test_client)

    assert _what_if_calls(fake) == []
    form = _form(section, "risk-what-if-form")
    assert 'method="get" action="/risk/what-if#risk-what-if"' in form
    assert re.search(
        r'<input type="text" id="risk-what-if-candidate" name="candidate" value=""', form
    )
    assert 'name="candidate_weight" value=""' in form
    assert 'inputmode="decimal"' in form
    # The tracked tickers are suggestions; the field takes any ticker.
    assert '<option value="EGLN.L">' in form
    options = re.findall(r'<option value="([^"]+)"( selected)?>([^<]+)</option>', form)
    assert options == [
        ("pro_rata", " selected", "Pro-rata from every holding"),
        ("replace:IWDA.AS", "", "Replace IWDA.AS"),
        ("replace:AAPL", "", "Replace AAPL"),
        ("replace:TSLA", "", "Replace TSLA"),
    ]
    assert "<table" not in section


def test_a_valid_form_sends_the_mix_candidate_window_and_exclusions(client):
    test_client, fake = client

    _get(
        test_client,
        "?candidate=EGLN.L&candidate_weight=7,5&funding=replace:AAPL"
        "&ex_ante_window=5Y&exclude=TSLA",
    )

    assert _what_if_calls(fake) == [
        ("what_if", _MIX, ("EGLN.L", Decimal("0.075")), "replace:AAPL", "5Y", ("TSLA",))
    ]


def test_stale_exclusions_are_dropped_for_the_what_if_as_for_ex_ante(client):
    test_client, fake = client

    query = f"{WHAT_IF_QUERY}&exclude=SOLD&exclude=TSLA&exclude=TSLA"

    _get(test_client, query)
    # UX-6: ex-ante is its own page; the same query sends it the same exclusions.
    test_client.get(f"/risk/ex-ante{query}")

    assert _what_if_calls(fake)[0][-1] == ("TSLA",)
    assert [c[-1] for c in fake.calls if c[0] == "ex_ante_risk"] == [("TSLA",)]


def test_an_excluded_holding_is_not_offered_to_replace(client):
    test_client, _ = client

    section = _get(test_client, "?exclude=TSLA")

    assert "replace:TSLA" not in section


def test_invalid_input_shows_inline_errors_keeps_it_and_calls_nothing(client):
    test_client, fake = client

    section = _get(test_client, "?candidate=AAPL&candidate_weight=150&funding=replace:MSFT")

    assert _what_if_calls(fake) == []
    form = _form(section, "risk-what-if-form")
    assert 'name="candidate" value="AAPL"' in form
    assert 'name="candidate_weight" value="150"' in form
    assert 'aria-invalid="true" aria-describedby="risk-what-if-candidate-error"' in form
    assert 'aria-invalid="true" aria-describedby="risk-what-if-weight-error"' in form
    assert 'aria-invalid="true" aria-describedby="risk-what-if-funding-error"' in form
    text = _text(form)
    assert "You already hold AAPL, so it can't be added to the mix." in text
    assert "Enter a weight above 0% and below 100%." in text
    assert "MSFT isn't one of your holdings. Choose again." in text
    assert "<table" not in section


def test_a_stale_replace_choice_is_an_error_not_pro_rata(client):
    test_client, fake = client

    section = _get(
        test_client, "?candidate=EGLN.L&candidate_weight=5&funding=replace:TSLA" "&exclude=TSLA"
    )

    assert _what_if_calls(fake) == []
    assert "TSLA is excluded above, so it can't fund EGLN.L. Choose again." in _text(section)


def test_the_form_keeps_the_other_controls_fields(client):
    # The benchmark form keeping the what-if's is in test_risk_page.py, where ex-post renders it.
    test_client, _ = client

    section = _get(
        test_client, f"{WHAT_IF_QUERY}&benchmark=IWDA.AS&ex_ante_window=5Y&exclude=TSLA&corr_a=AAPL"
    )

    assert _hidden(_form(section, "risk-what-if-form")) == [
        ("benchmark", "IWDA.AS"),
        ("ex_ante_window", "5Y"),
        ("exclude", "TSLA"),
        # RK-25's rolling pair.
        ("corr_a", "AAPL"),
    ]


# --- the section ------------------------------------------------------------------------


def test_the_table_shows_before_after_and_delta_risk_first(client):
    test_client, _ = client

    section = _get(test_client, WHAT_IF_QUERY)

    rows = re.findall(r"<tr role=\"row\">.*?</tr>", section.split("<tbody", 1)[1], re.S)
    texts = [_text(row) for row in rows]
    assert texts == [
        "Volatility weekly 14.1% 13.9% −0.2 pts",
        "Diversification ratio weekly 1.16 1.25 +0.09",
        "EGLN.L's share of risk weekly — 3.1% —",
        "EGLN.L's correlation with the mix weekly — 0.12 —",
        "Sharpe ratio weekly · ± 1 s.e. 0.80 0.88 +0.08 ± 0.10",
        "Max drawdown daily −23.5% −22.1% +1.3 pts",
    ]
    # Every figure in the figure font, and none coloured as a gain or a loss.
    assert not re.search(r'class="[^"]*\b(gain|loss)\b', section)
    assert '<span class="mono">+0.09</span>' in section
    # Each cell's title carries its metadata (spec §10).
    assert (
        'title="3Y: 2023-10-20 to 2026-10-16 · weekly · 156 obs · 0 gaps · ex-ante · constant'
        ' weights"' in section
    )
    text = _text(section)
    assert "Measured from 2023-10-20 to 2026-10-16" in text
    assert "historical" in text and "expected" not in text


def test_the_threshold_under_pro_rata_gives_its_inputs_and_verdict(client):
    test_client, _ = client

    text = _text(_get(test_client, WHAT_IF_QUERY))

    assert (
        "A small amount of EGLN.L raises the mix's historical Sharpe ratio if its own Sharpe"
        " ratio is above its correlation with the mix times the mix's: 0.51 ± 0.58 against"
        " 0.12 × 0.80 = 0.10. It is, so a small amount of EGLN.L would have raised it." in text
    )


def test_a_threshold_not_met_says_so(client):
    test_client, fake = client
    fake.what_if_result = _what_if(threshold=replace(_THRESHOLD, improves=False))

    text = _text(_get(test_client, WHAT_IF_QUERY))

    assert "It isn't, so a small amount of EGLN.L wouldn't have raised it." in text


def test_a_greyed_threshold_gives_its_reason(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        threshold=WhatIfThreshold(
            True,
            "the candidate's and the holdings' histories don't overlap",
            None,
            None,
            None,
            None,
            None,
        )
    )

    text = _text(_get(test_client, WHAT_IF_QUERY))

    assert (
        "The Sharpe threshold can't be shown: the candidate's and the holdings' histories"
        " don't overlap." in text
    )


def test_replace_funding_explains_why_there_is_no_threshold_and_a_capped_weight(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        candidate=replace(_CANDIDATE, weight=0.05, capped=True),
        funding=WhatIfFunding("replace", "TSLA", None),
        threshold=WhatIfThreshold(
            False, "shown under pro-rata funding only", None, None, None, None, None
        ),
    )

    text = _text(_get(test_client, "?candidate=EGLN.L&candidate_weight=10&funding=replace:TSLA"))

    assert (
        "Replacing TSLA can fund at most its own weight, so EGLN.L enters at 5.0%, not 10.0%."
        in text
    )
    assert "The Sharpe threshold is shown under pro-rata funding only." in text


def test_survivorship_warns_once_and_marks_the_after_figures(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        candidate=replace(_CANDIDATE, survivorship=True), rows=_rows(flags=("survivorship",))
    )

    section = _get(test_client, WHAT_IF_QUERY)

    text = _text(section)
    assert R14 in text
    assert '<sup class="risk-flag">1</sup>' in section
    assert "today's index members" in _text(section.split('class="risk-legend"', 1)[1])


def test_a_shortened_window_says_before_was_measured_on_it_too(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        window=replace(
            _WINDOW,
            start=date(2024, 6, 7),
            limited_by=("EGLN.L",),
            shortened=True,
            start_without_candidate=START,
        )
    )

    text = _text(_get(test_client, WHAT_IF_QUERY))

    assert (
        "Measured from 2024-06-07 to 2026-10-16: EGLN.L's history starts later than the"
        " holdings' (2023-10-20), so before is measured over the same shorter window." in text
    )


def test_holdings_portfolio_couldnt_measure_are_listed(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "included", None),
            BacktestAssetStatus("TSLA", "no_fx_rate", "no FX series for USD"),
        )
    )

    text = _text(_get(test_client, WHAT_IF_QUERY))

    assert "TSLA — no FX series for USD" in text


def test_nothing_to_compare_says_why(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        candidate=WhatIfCandidate(
            "0700.HK", "no_fx_rate", "no FX series for HKD", 0.1, None, None, False
        ),
        window=None,
        rows=None,
        threshold=None,
    )

    section = _get(test_client, "?candidate=0700.HK&candidate_weight=10")

    assert "Nothing to compare: 0700.HK can't be measured: no FX series for HKD." in _text(section)
    assert "<table" not in section


# --- empty and error states -------------------------------------------------------------


def test_a_failed_what_if_shows_a_notice_in_its_section_only(client):
    test_client, fake = client
    fake.what_if_error = PortfolioUnavailable("down")

    page = test_client.get(f"/risk/what-if{WHAT_IF_QUERY}").text

    section = _section(page)
    assert "Couldn't compare." in _text(section)
    # The form stays, so the user can try again.
    assert 'id="risk-what-if-form"' in section
    assert "Couldn't load the ex-ante figures." not in page


def test_a_failed_ex_ante_never_reaches_the_what_if_page(client):
    """UX-6: ex-ante is its own page, so the what-if page doesn't call it."""
    test_client, fake = client
    fake.ex_ante_error = PortfolioUnavailable("down")

    page = test_client.get(f"/risk/what-if{WHAT_IF_QUERY}").text

    assert "Couldn't load the ex-ante figures." not in page
    assert "<table" in _section(page)


def test_a_failed_holdings_call_shows_a_notice_and_no_form(client):
    test_client, fake = client
    fake.holdings_error = PortfolioUnavailable("down")

    section = _get(test_client, WHAT_IF_QUERY)

    assert "Couldn't load your holdings to compare against." in _text(section)
    assert "<form" not in section
    assert _what_if_calls(fake) == []


def test_no_holdings_to_compare_against(client):
    test_client, fake = client
    fake.holdings = Holdings(holdings=(), totals=None)

    section = _get(test_client, WHAT_IF_QUERY)

    assert "Nothing to compare against yet" in _text(section)
    assert "<form" not in section
    assert _what_if_calls(fake) == []


# --- logging ----------------------------------------------------------------------------


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "web.api.risk"]


def test_logs_the_request_and_outcome(client, caplog):
    test_client, fake = client
    fake.what_if_result = _what_if(
        candidate=replace(_CANDIDATE, survivorship=True),
        window=replace(_WINDOW, limited_by=("EGLN.L",), shortened=True),
    )

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _get(test_client, WHAT_IF_QUERY)

    messages = _messages(caplog)
    assert (
        "risk: loading the what-if for EGLN.L at 10% of the mix, funding pro_rata, over 3Y, "
        "3 holdings, excluding none." in messages
    )
    assert (
        "risk: what-if EGLN.L over 3Y, 2023-10-20 to 2026-10-16, shortened by EGLN.L, "
        "not capped, survivorship flagged, 0 of 10 figures greyed, threshold met, "
        "left out none." in messages
    )


def test_logs_a_form_with_errors_and_nothing_sent(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _get(test_client, "?candidate=&candidate_weight=10")

    assert "risk: what-if form has errors (ticker), nothing sent." in _messages(caplog)


def test_logs_an_empty_form(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _get(test_client)

    assert "risk: what-if form empty, nothing sent." in _messages(caplog)


def test_logs_nothing_to_compare(client, caplog):
    test_client, fake = client
    fake.what_if_result = _what_if(
        funding=WhatIfFunding("replace", "TSLA", "TSLA, the holding to replace, can't be measured"),
        window=None,
        rows=None,
        threshold=None,
    )

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _get(test_client, "?candidate=EGLN.L&candidate_weight=10&funding=replace:TSLA")

    assert (
        "risk: what-if had nothing to compare: TSLA, the holding to replace, can't be measured."
        in _messages(caplog)
    )


# --- ticker names (UX-2) ------------------------------------------------------------------------

_NAMED = '<span class="ticker" tabindex="0"><span class="mono">{}</span>'


def test_the_candidate_field_suggests_each_ticker_with_its_name(client):
    """An option can't hold the tooltip, so it carries the name; the value stays the ticker."""
    test_client, fake = client
    fake.tracked_names = {"EGLN.L": "iShares Physical Gold"}

    section = _get(test_client)

    datalist = re.search(r'<datalist id="risk-what-if-options">(.*?)</datalist>', section, re.S)
    assert '<option value="EGLN.L" label="EGLN.L — iShares Physical Gold">' in datalist[1]
    assert '<option value="IWDA.AS">' in datalist[1]


def test_the_candidate_and_its_funding_are_named(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        candidate=replace(_CANDIDATE, weight=0.05, capped=True),
        funding=WhatIfFunding("replace", "TSLA", None),
        threshold=WhatIfThreshold(
            False, "shown under pro-rata funding only", None, None, None, None, None
        ),
    )
    fake.tracked_names = {"EGLN.L": "iShares Physical Gold", "TSLA": "Tesla, Inc."}

    section = _get(test_client, "?candidate=EGLN.L&candidate_weight=10&funding=replace:TSLA")

    header = re.search(r"<th[^>]*>After, with .*?</th>", section, re.S).group(0)
    assert _NAMED.format("EGLN.L") in header and "iShares Physical Gold" in header
    note = re.search(r"<p[^>]*>Replacing .*?</p>", section, re.S).group(0)
    assert _NAMED.format("TSLA") in note and "Tesla, Inc." in note
    assert _NAMED.format("EGLN.L") in note


def test_a_holding_the_what_if_couldnt_measure_is_named(client):
    test_client, fake = client
    fake.what_if_result = _what_if(
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "included", None),
            BacktestAssetStatus("TSLA", "no_fx_rate", "no FX series for USD"),
        )
    )
    fake.tracked_names = {"TSLA": "Tesla, Inc."}

    section = _get(test_client, WHAT_IF_QUERY)

    item = re.search(r"<li>(?:(?!</li>).)*no FX series for USD.*?</li>", section, re.S).group(0)
    assert _NAMED.format("TSLA") in item


# --- copy (UX-9) ---------------------------------------------------------------------------------

HOVER = "Hover a figure for its window and number of returns"


def _intro(section: str) -> str:
    return re.search(r'<p class="entry-subtitle">(.*?)</p>', section, re.S).group(1)


def _sentences(markup: str) -> list[str]:
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s]


def test_the_intro_is_one_historical_sentence_with_its_glossary_link(client):
    test_client, _ = client

    intro = _intro(_get(test_client))

    assert len(_sentences(intro)) == 1, _sentences(intro)
    assert 'href="/glossary#what-if"' in intro
    assert "historical" in intro and "expected" not in intro


def test_the_hover_note_shows_once_with_both_the_what_if_and_the_ranking(client):
    test_client, fake = client
    fake.ranking_result = CandidateRanking(
        label=LABEL,
        rf_available=True,
        statuses=tuple(BacktestAssetStatus(t, "included", None) for t, _ in _MIX),
        nothing=None,
        universe=0,
        cap=50,
        over_cap=(),
        weight=0.05,
        rank_by="sigma_p",
        window=RankingWindow("3Y", date(2023, 10, 16), START, END),
        ranked=(),
        greyed=(),
        unranked=(),
    )

    response = test_client.get(f"/risk/what-if{WHAT_IF_QUERY}&rank_weight=5")

    assert response.status_code == 200
    assert "Measured from 2023-10-20" in _text(_section(response.text))  # the what-if ran
    assert "Each ticker at" in _text(response.text)  # and the ranking did
    assert _text(response.text).count(HOVER) == 1
