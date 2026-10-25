"""risk/flows.py's ex-ante flow (RK-17): fetch a mix's holdings, the benchmark and the risk-free
series, give every requested asset a status, normalise the weights over those included, and
hand the rest to risk/ex_ante_report.py, whose windows are tested in test_ex_ante_report.py.

Calendar: 2018-01-05 is a Friday. Every ticker's prices are the weekdays after its first date,
with returns from a fixed seed.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from portfolio.datastore_client.base import (
    FxPoint,
    PricePoint,
    Regime,
    RegimeList,
    RiskFreePoint,
    RiskFreeSeries,
    TickerInfo,
)
from portfolio.errors import DataStoreApiError
from portfolio.eur_returns.calculator import eur_daily_returns
from portfolio.risk import flows
from portfolio.risk.ex_ante_report import NO_OVERLAP
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.rolling import rolling_correlation
from portfolio.risk.types import Frequency, ReturnSeries
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import StandardWindow

FIRST = date(2018, 1, 5)
TODAY = date(2022, 3, 1)
RF = RiskFreeSeries(
    first_date=date(1999, 1, 4),
    points=[RiskFreePoint(date(2017, 12, 1), Decimal("-0.5000"), "estr")],
)
EUR = TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
USD = TickerInfo(exists=True, has_returns=True, currency="USD", fx_currency="USD")
UNTRACKED = TickerInfo(exists=False, has_returns=False)
NO_RETURNS = TickerInfo(exists=True, has_returns=False, currency="EUR", fx_currency="EUR")


def _weekdays(first, end=TODAY):
    days, day = [], first
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days


def _prices(seed, first=FIRST):
    days = _weekdays(first)
    returns = np.random.default_rng(seed).normal(0.0003, 0.01, len(days))
    return [PricePoint(first, Decimal("100"), None)] + [
        PricePoint(d, Decimal("100"), Decimal(str(round(float(r), 6))))
        for d, r in zip(days, returns)
    ]


def _fx(seed):
    days = [FIRST, *_weekdays(FIRST)]
    moves = np.random.default_rng(seed).normal(0, 0.005, len(days))
    return [
        FxPoint(d, Decimal(str(round(1.1 * float(np.prod(1 + moves[: k + 1])), 6))))
        for k, d in enumerate(days)
    ]


COVID = Regime(
    "spx_bear_2020",
    "drawdown",
    "SPX",
    "COVID crash",
    date(2020, 2, 19),
    date(2020, 3, 23),
    None,
    "severe",
    "manual_approx",
    None,
)
GFC = Regime(
    "gfc", "event", "GLOBAL", "GFC", date(2007, 8, 9), date(2009, 3, 9), None, None, "manual", None
)
REGIMES = RegimeList(94, [COVID])


class _FakeDataStoreClient:
    """`tickers` maps a ticker to (TickerInfo, prices or an exception to raise)."""

    def __init__(self, tickers, fx=None, risk_free=RF, regimes=REGIMES):
        self._tickers = {"VWCE.DE": (EUR, _prices(99)), **tickers}
        self._fx = fx or {}
        self._risk_free = risk_free
        self._regimes = regimes
        self.risk_free_calls = []
        self.price_calls = []
        self.regime_calls = []

    def get_regimes(self, start, end):
        self.regime_calls.append((start, end))
        if isinstance(self._regimes, Exception):
            raise self._regimes
        return self._regimes

    def get_ticker_info(self, ticker):
        return self._tickers.get(ticker, (UNTRACKED, None))[0]

    def get_price_series(self, ticker, start, end):
        self.price_calls.append(ticker)
        prices = self._tickers[ticker][1]
        if isinstance(prices, Exception):
            raise prices
        return prices

    def get_fx_rate_series(self, base, quote, start, end):
        return self._fx.get(quote, [])

    def get_risk_free_series(self, start, end):
        self.risk_free_calls.append((start, end))
        if isinstance(self._risk_free, Exception):
            raise self._risk_free
        return self._risk_free


def _run(client, mix, **kw):
    kw.setdefault("window", StandardWindow.Y3)
    return flows.compute_ex_ante(client, [(t, Decimal(w)) for t, w in mix], today=TODAY, **kw)


def _statuses(result):
    return [(s.ticker, s.status, s.reason) for s in result.statuses]


def test_every_requested_asset_gets_a_status_in_input_order(caplog):
    client = _FakeDataStoreClient(
        {
            "A": (EUR, _prices(1)),
            "X": (EUR, _prices(2)),
            "H": (NO_RETURNS, None),
            "F": (EUR, DataStoreApiError("boom")),
            "U": (USD, _prices(3)),
        }
    )
    mix = [("A", "1"), ("X", "1"), ("N", "1"), ("H", "1"), ("F", "1"), ("U", "1")]

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client, mix, exclude={"X"})

    assert _statuses(result) == [
        ("A", "included", None),
        ("X", "excluded", "excluded by request"),
        ("N", "not_tracked", "not tracked"),
        ("H", "no_history", "no history yet"),
        ("F", "failed", "couldn't reach data-store"),
        ("U", "no_fx_rate", "no FX series for USD"),
    ]
    assert "X" not in client.price_calls  # excluded before the fetch
    assert "ex-ante-risk: A: included" in caplog.text
    assert "ex-ante-risk: X: excluded by request." in caplog.text
    assert "ex-ante-risk: U: left out, no FX series for USD." in caplog.text
    failure = next(r for r in caplog.records if "F: failed" in r.getMessage())
    assert failure.levelno == logging.ERROR and failure.exc_info is not None


def test_var_leaves_out_a_holdings_zero_volume_flat_days(caplog):
    # RK-31: B's Mondays are stale holiday rows (zero volume, 0%). They stay in B's returns but
    # leave VaR's sample; with real volume on them, they'd count. The per-ticker log says how
    # many days each holding didn't trade.
    def b_prices(holiday_volume):
        return [
            (
                PricePoint(p.date, p.split_adjusted_close, Decimal("0"), holiday_volume)
                if p.date.weekday() == 0 and p.date != FIRST
                else PricePoint(p.date, p.split_adjusted_close, p.simple_return, 1000)
            )
            for p in _prices(2)
        ]

    def var(holiday_volume):
        client = _FakeDataStoreClient(
            {"A": (EUR, _prices(1)), "B": (EUR, b_prices(holiday_volume))}
        )
        report = _run(client, [("A", "1"), ("B", "1")]).report
        return report.window, report.constant_weight.tail.var_95

    window, traded = var(1000)
    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        _, masked = var(0)
    mondays = [d for d in _weekdays(FIRST) if d.weekday() == 0 and window.start < d <= window.end]
    assert masked.n_obs == traded.n_obs - len(mondays)
    all_mondays = sum(1 for d in _weekdays(FIRST) if d.weekday() == 0)
    days = len(_weekdays(FIRST))
    assert f"ex-ante-risk: A: included, {days} day(s) of EUR returns, 0 not traded." in caplog.text
    assert (
        f"ex-ante-risk: B: included, {days} day(s) of EUR returns, {all_mondays} not traded."
        in caplog.text
    )


def test_the_weights_are_normalised_over_the_included_holdings():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2))})

    result = _run(client, [("A", "3"), ("N", "4"), ("B", "1")])

    assert result.weights == pytest.approx({"A": 0.75, "B": 0.25})
    holdings = {h.key: h.weight for h in result.report.decomposition.holdings}
    assert holdings == pytest.approx({"A": 0.75, "B": 0.25})


def test_local_currency_changes_only_the_correlation_matrix():
    client = _FakeDataStoreClient(
        {"A": (EUR, _prices(1)), "B": (USD, _prices(2))}, fx={"USD": _fx(7)}
    )
    mix = [("A", "1"), ("B", "1")]

    eur = _run(client, mix)
    local = _run(client, mix, currency="local")

    assert (eur.currency, local.currency) == ("eur", "local")
    assert local.report.decomposition.sigma_p == eur.report.decomposition.sigma_p
    assert local.report.constant_weight == eur.report.constant_weight
    assert local.report.correlation.cell("A", "A").value == pytest.approx(1.0)
    assert local.report.correlation.cell("A", "B").value != pytest.approx(
        eur.report.correlation.cell("A", "B").value
    )
    # A EUR ticker's local return is its EUR return.
    assert local.report.correlation.cell("A", "VWCE.DE") == eur.report.correlation.cell(
        "A", "VWCE.DE"
    )


def test_the_benchmark_joins_the_matrix_after_the_holdings():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2))})

    result = _run(client, [("A", "1"), ("B", "1")])

    assert result.report.correlation.keys == ("A", "B", "VWCE.DE")
    assert result.benchmark_key == "VWCE.DE"
    assert result.benchmark.available is True
    assert result.benchmark.first_date == FIRST


def test_a_benchmark_that_is_also_a_holding_is_not_repeated():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1))})

    result = _run(client, [("VWCE.DE", "1"), ("A", "1")])

    assert result.report.correlation.keys == ("VWCE.DE", "A")
    assert result.benchmark_key == "VWCE.DE"


def test_an_unavailable_benchmark_leaves_the_matrix_to_the_holdings_and_says_why():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2))})

    result = _run(client, [("A", "1"), ("B", "1")], benchmark="NOPE")

    assert result.report.correlation.keys == ("A", "B")
    assert result.benchmark_key is None
    assert (result.benchmark.ticker, result.benchmark.available) == ("NOPE", False)
    assert result.benchmark.reason == "not tracked"
    assert result.report.decomposition.sigma_p.value is not None


def test_the_risk_free_fetch_starts_two_weeks_before_the_oldest_holding():
    late = date(2019, 3, 4)
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2, first=late))})

    result = _run(client, [("A", "1"), ("B", "1")], window=StandardWindow.MAX)

    assert client.risk_free_calls == [(FIRST - timedelta(days=14), result.report.requested.end)]
    assert result.rf_available is True
    assert result.report.limited_by == ("B",)
    assert result.report.constant_weight.weekly.sharpe.value is not None


@pytest.mark.parametrize("risk_free", [None, DataStoreApiError("down")], ids=["none", "failed"])
def test_without_risk_free_data_the_excess_return_figures_are_greyed(risk_free):
    client = _FakeDataStoreClient({"A": (EUR, _prices(1))}, risk_free=risk_free)

    result = _run(client, [("A", "1")])

    assert result.rf_available is False
    weekly = result.report.constant_weight.weekly
    assert weekly.sharpe.reason == NO_RISK_FREE
    assert weekly.volatility.value is not None


def test_nothing_included_means_no_report_and_no_further_fetches(caplog):
    client = _FakeDataStoreClient({})

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client, [("N", "1")])

    assert result.report is None
    assert result.weights == {}
    assert result.rf_available is False
    assert result.benchmark.available is False
    assert result.benchmark.reason == "not fetched: nothing to measure"
    assert client.risk_free_calls == []
    assert "VWCE.DE" not in client.price_calls
    assert "ex-ante-risk: no holding could be included, so nothing to measure." in caplog.text


def test_logs_a_start_line_the_window_and_a_summary(caplog):
    late = date(2019, 3, 4)
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2, first=late))})

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        _run(client, [("A", "1"), ("B", "1")], window=StandardWindow.MAX)

    assert (
        "ex-ante-risk: 2 asset(s), 0 excluded by request, window Max, against VWCE.DE, "
        "correlation in eur." in caplog.text
    )
    assert "ex-ante-risk: window Max: requested 2018-01-05.." in caplog.text
    assert "limited by B" in caplog.text
    assert "ex-ante-risk: done, 2 holding(s) included, 0 left out" in caplog.text


# --- rolling (RK-24) --------------------------------------------------------------------------


def _weekly_eur(prices, fx=None):
    """A ticker's weekly EUR returns, as the flow builds them from RK-5's r_eur."""
    returns = eur_daily_returns(prices, fx)
    daily = ReturnSeries(
        Frequency.DAILY,
        returns.anchor,
        tuple(returns.dates),
        tuple(None if r is None else float(r) for r in returns.r_eur),
    )
    return to_weekly(daily)


def test_the_mix_has_rolling_volatility_and_sharpe_and_no_pair_unless_asked():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2))})

    result = _run(client, [("A", "1"), ("B", "1")])

    assert result.report.rolling.volatility
    assert all(f.value is not None for f in result.report.rolling.sharpe)
    assert result.rolling_correlation is None


@pytest.mark.parametrize("currency", ["eur", "local"])
def test_a_rolling_pair_is_measured_in_eur_whatever_the_matrix_currency(currency, caplog):
    fx = _fx(7)
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "U": (USD, _prices(2))}, fx={"USD": fx})

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client, [("A", "1"), ("U", "1")], currency=currency, rolling_pair=("A", "U"))

    pair = result.rolling_correlation
    assert (pair.a, pair.b, pair.reason) == ("A", "U", None)
    assert pair.points == rolling_correlation(_weekly_eur(_prices(1)), _weekly_eur(_prices(2), fx))
    assert pair.points
    gaps = sum(1 for p in pair.points if p.value is None)
    assert (
        f"ex-ante-risk: rolling correlation A vs U: {len(pair.points)} weekly point(s), "
        f"{gaps} greyed." in caplog.text
    )


def test_one_asset_against_the_portfolio_uses_the_constant_weight_mix():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2))})

    result = _run(client, [("A", "1"), ("B", "3")], rolling_pair=("A", None))

    pair = result.rolling_correlation
    assert (pair.a, pair.b) == ("A", flows.PORTFOLIO)
    assert pair.points == rolling_correlation(_weekly_eur(_prices(1)), result.report.mix_weekly)


def test_a_rolling_pair_can_name_the_benchmark():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1))})

    result = _run(client, [("A", "1")], rolling_pair=("VWCE.DE", "A"))

    assert result.rolling_correlation.points == rolling_correlation(
        _weekly_eur(_prices(99)), _weekly_eur(_prices(1))
    )


def test_a_left_out_ticker_in_the_pair_says_why_and_has_no_points():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "H": (NO_RETURNS, None)})

    result = _run(client, [("A", "1"), ("H", "1")], rolling_pair=("A", "H"))

    pair = result.rolling_correlation
    assert pair.points == ()
    assert pair.reason == "H not included: no history yet"


def test_an_unavailable_benchmark_in_the_pair_says_why():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1))})

    result = _run(client, [("A", "1")], benchmark="NOPE", rolling_pair=("A", "NOPE"))

    assert result.rolling_correlation.points == ()
    assert (
        result.rolling_correlation.reason == "NOPE not included: benchmark unavailable: not tracked"
    )


def test_against_the_portfolio_when_the_holdings_do_not_overlap_says_why():
    client = _FakeDataStoreClient(
        {
            "A": (EUR, _prices(1)[:300]),
            "B": (EUR, _prices(2, first=date(2020, 6, 5))),
        }
    )

    result = _run(client, [("A", "1"), ("B", "1")], rolling_pair=("B", None))

    assert result.rolling_correlation.points == ()
    assert result.rolling_correlation.reason == "portfolio not included: " + NO_OVERLAP


# --- FX decomposition (RK-26) -----------------------------------------------------------------


def test_each_non_eur_holding_is_decomposed_from_its_own_local_and_fx_returns(caplog):
    prices, fx = _prices(2), _fx(7)
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (USD, prices)}, fx={"USD": fx})

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client, [("A", "1"), ("B", "1")])

    (row,) = result.report.fx_decomposition
    assert (row.ticker, row.currency) == ("B", "USD")
    returns = eur_daily_returns(prices, fx)
    local = to_weekly(
        ReturnSeries(
            Frequency.DAILY,
            returns.anchor,
            tuple(returns.dates),
            tuple(None if r is None else float(r) for r in returns.r_local),
        )
    )
    panel = result.report.decomposition.panel
    on_panel = [v for d, v in zip(local.dates, local.values) if d in panel.dates]
    assert row.local_volatility.value == pytest.approx(np.std(on_panel, ddof=1) * np.sqrt(52))
    b = next(h for h in result.report.decomposition.holdings if h.key == "B")
    assert row.eur_volatility.value == pytest.approx(b.volatility, rel=1e-12)
    assert (
        "ex-ante-risk: FX decomposition of 1 non-EUR holding(s), 0 of 4 figure(s) greyed."
        in caplog.text
    )


def test_an_all_eur_mix_has_no_fx_rows_and_says_so(caplog):
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2))})

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client, [("A", "1"), ("B", "1")])

    assert result.report.fx_decomposition == ()
    assert "ex-ante-risk: no FX decomposition, every included holding is in EUR." in caplog.text


def test_a_one_year_window_greys_each_fx_correlation(caplog):
    client = _FakeDataStoreClient(
        {"A": (EUR, _prices(1)), "B": (USD, _prices(2))}, fx={"USD": _fx(7)}
    )

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client, [("A", "1"), ("B", "1")], window=StandardWindow.Y1)

    (row,) = result.report.fx_decomposition
    assert row.correlation.value is None
    assert row.eur_volatility.value is not None
    assert "1 of 4 figure(s) greyed" in caplog.text


# --- the regime view (RK-28) ------------------------------------------------------------------


def test_regimes_run_on_the_daily_mix_over_its_whole_history_whatever_the_window():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1)), "B": (EUR, _prices(2))})

    result = _run(client, [("A", "1"), ("B", "3")], window=StandardWindow.Y1)

    ((start, end),) = client.regime_calls
    assert start == FIRST  # Max's start, not the 1Y window's
    (entry,) = result.regimes.entries
    assert entry.regime == COVID
    row = entry.row
    assert (row.measured.start, row.measured.end) == (COVID.start_date, COVID.end_date)
    assert (row.partial, row.ongoing) == (False, False)
    a, b = (eur_daily_returns(client._tickers[t][1], None) for t in ("A", "B"))
    inside = [
        0.25 * float(ra) + 0.75 * float(rb)
        for d, ra, rb in zip(a.dates, a.r_eur, b.r_eur)
        if COVID.start_date < d <= COVID.end_date
    ]
    assert row.portfolio.cumulative_return.value == pytest.approx(np.prod(1 + np.array(inside)) - 1)
    assert row.benchmark.cumulative_return.value is not None


def test_a_regime_before_the_mix_starts_is_not_shown():
    client = _FakeDataStoreClient(
        {"A": (EUR, _prices(1)), "B": (EUR, _prices(2))}, regimes=RegimeList(94, [GFC, COVID])
    )

    result = _run(client, [("A", "1"), ("B", "1")])

    assert [entry.regime.regime_id for entry in result.regimes.entries] == ["spx_bear_2020"]


def test_an_unavailable_benchmark_greys_the_regime_benchmark_figures_only():
    client = _FakeDataStoreClient({"A": (EUR, _prices(1))})

    result = _run(client, [("A", "1")], benchmark="NOPE")

    row = result.regimes.entries[0].row
    assert row.portfolio.max_drawdown.value is not None
    assert row.benchmark.max_drawdown.reason == "benchmark unavailable: not tracked"


def test_a_failed_regimes_fetch_still_measures_the_rest(caplog):
    client = _FakeDataStoreClient({"A": (EUR, _prices(1))}, regimes=DataStoreApiError("boom"))

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = _run(client, [("A", "1")])

    assert result.regimes == flows.RegimeView(False, flows.REGIMES_UNAVAILABLE, None, ())
    assert result.report.decomposition.sigma_p.value is not None
    assert (
        "ex-ante-risk: couldn't fetch the regimes, so there is no regime view: boom" in caplog.text
    )


def test_regimes_log_with_the_ex_ante_context(caplog):
    client = _FakeDataStoreClient({"A": (EUR, _prices(1))})

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        _run(client, [("A", "1")])

    assert "ex-ante-risk: regimes: 94 loaded, 1 overlapping 2018-01-05.." in caplog.text
    assert "ex-ante-risk: regime spx_bear_2020 (2020-02-19..2020-03-23): measured" in caplog.text
    assert "ex-ante-risk: regimes: 1 shown, 0 not shown, benchmark greyed on 0." in caplog.text


def test_when_the_holdings_do_not_overlap_regimes_are_not_fetched():
    client = _FakeDataStoreClient(
        {"A": (EUR, _prices(1)[:300]), "B": (EUR, _prices(2, first=date(2020, 6, 5)))}
    )

    result = _run(client, [("A", "1"), ("B", "1")])

    assert result.regimes == flows.RegimeView(False, NO_OVERLAP, None, ())
    assert client.regime_calls == []


def test_with_nothing_included_there_is_no_regime_view():
    client = _FakeDataStoreClient({})

    result = _run(client, [("N", "1")])

    assert result.regimes is None
    assert client.regime_calls == []
