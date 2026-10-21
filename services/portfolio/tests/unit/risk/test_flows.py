"""risk/flows.py: the I/O around risk/ex_post.py (RK-11). It takes the user's TWR from
twr/flows.py, fetches the risk-free series, converts both to the library's floats, and logs
each window's outcome. The figures themselves are tested in test_ex_post.py; the TWR's reads and
fetches in tests/unit/twr/test_flows.py, so compute_portfolio_twr is faked here.

Calendar: 2020-01-03 is a Friday. The fake TWR alternates +1% and -1% on weekdays.
"""

import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from portfolio.datastore_client.base import RiskFreePoint, RiskFreeSeries
from portfolio.errors import DataStoreApiError
from portfolio.risk import flows
from portfolio.risk.types import Flag
from portfolio.risk.windows import years_before
from portfolio.twr.calculator import TwrDay, TwrPoint
from portfolio.twr.flows import ExcludedHolding, PortfolioTwr

BASE = date(2020, 1, 3)
TODAY = date(2022, 2, 1)
RF = RiskFreeSeries(
    first_date=date(1999, 1, 4),
    points=[RiskFreePoint(date(2019, 12, 20), Decimal("-0.5400"), "estr")],
)


def _weekdays(base, n):
    days, day = [], base
    while len(days) < n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days


def _twr(n=530, *, gap_on=None, excluded=()):
    dates = _weekdays(BASE, n)
    points, days = [], []
    for k, d in enumerate(dates):
        value = None if d == gap_on else Decimal("0.01" if k % 2 == 0 else "-0.01")
        points.append(TwrPoint(d, value, Decimal("1")))  # growth isn't read by the risk path
        days.append(TwrDay(d, frozenset({"A"}), True))
    return PortfolioTwr(
        base_date=BASE,
        points=points,
        n_gaps=0 if gap_on is None else 1,
        excluded=list(excluded),
        days=tuple(days),
        currencies={"A": "EUR"},
    )


class _FakeDataStoreClient:
    def __init__(self, risk_free=RF):
        self._risk_free = risk_free
        self.calls = []

    def get_risk_free_series(self, start, end):
        self.calls.append((start, end))
        if isinstance(self._risk_free, Exception):
            raise self._risk_free
        return self._risk_free


def _fake_twr(monkeypatch, twr):
    captured = {}

    def compute_portfolio_twr(conn, client, **kw):
        captured.update(kw, conn=conn, client=client)
        return twr

    monkeypatch.setattr(flows.twr_flows, "compute_portfolio_twr", compute_portfolio_twr)
    return captured


def _by_label(result):
    return {w.window.label: w for w in result.windows}


def test_measures_the_users_twr_and_labels_it_ex_post_invested_assets(monkeypatch):
    user_id = uuid.uuid4()
    conn = object()
    client = _FakeDataStoreClient()
    excluded = [ExcludedHolding("U", "no_fx_rate", "no FX series for HKD")]
    captured = _fake_twr(monkeypatch, _twr(excluded=excluded))

    result = flows.compute_ex_post(conn, client, user_id=user_id, today=TODAY)

    assert captured == {"user_id": user_id, "today": TODAY, "conn": conn, "client": client}
    assert result.label == "ex-post · invested assets"
    assert result.base_date == BASE
    assert result.end_date == _weekdays(BASE, 530)[-1]
    assert result.rf_available is True
    assert result.excluded == excluded
    assert [w.window.label for w in result.windows] == ["1Y", "3Y", "5Y", "10Y", "Max"]
    assert _by_label(result)["1Y"].weekly.sharpe.value is not None


def test_the_risk_free_fetch_starts_before_the_base_date_so_the_first_week_has_a_rate(monkeypatch):
    # risk_free_returns forward-fills from the latest rate on or before each day, and refuses a
    # day after 1999 with none. A base date after a weekend or holiday needs an earlier point.
    client = _FakeDataStoreClient()
    _fake_twr(monkeypatch, _twr())

    flows.compute_ex_post(object(), client, user_id=uuid.uuid4(), today=TODAY)

    assert client.calls == [(BASE - timedelta(days=14), _weekdays(BASE, 530)[-1])]


def test_a_null_twr_return_stays_a_gap(monkeypatch):
    gap = _weekdays(BASE, 530)[300]
    _fake_twr(monkeypatch, _twr(gap_on=gap))

    result = flows.compute_ex_post(object(), _FakeDataStoreClient(), user_id=uuid.uuid4())

    max_window = _by_label(result)["Max"]
    assert max_window.drawdown.max_drawdown.n_gaps == 1
    assert max_window.drawdown.max_drawdown.n_obs == 529


def test_no_risk_free_data_greys_the_rf_measures_and_warns(monkeypatch, caplog):
    _fake_twr(monkeypatch, _twr())

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = flows.compute_ex_post(object(), _FakeDataStoreClient(None), user_id=uuid.uuid4())

    one_year = _by_label(result)["1Y"]
    assert result.rf_available is False
    assert one_year.weekly.volatility.value is not None
    assert one_year.weekly.sharpe.reason == "no risk-free rate data"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == [
        "ex-post-risk: data-store has no risk-free data stored; downside deviation, Sharpe and "
        "Sortino are greyed."
    ]


def test_a_failed_risk_free_fetch_greys_the_rf_measures_and_logs_the_error(monkeypatch, caplog):
    _fake_twr(monkeypatch, _twr())
    client = _FakeDataStoreClient(DataStoreApiError("boom"))

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = flows.compute_ex_post(object(), client, user_id=uuid.uuid4())

    assert result.rf_available is False
    assert _by_label(result)["1Y"].weekly.sortino.reason == "no risk-free rate data"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert (
        errors[0]
        .getMessage()
        .startswith(
            "ex-post-risk: couldn't fetch the risk-free series, so downside deviation, Sharpe and "
            "Sortino are greyed: boom"
        )
    )
    assert errors[0].exc_info is not None


@pytest.mark.parametrize(
    "twr",
    [
        PortfolioTwr(base_date=None, points=[], n_gaps=0, excluded=[]),
        PortfolioTwr(
            base_date=None,
            points=[],
            n_gaps=0,
            excluded=[ExcludedHolding("U", "no_fx_rate", "no FX series for HKD")],
        ),
    ],
)
def test_no_twr_points_means_no_windows_and_no_risk_free_fetch(monkeypatch, caplog, twr):
    _fake_twr(monkeypatch, twr)
    client = _FakeDataStoreClient()

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = flows.compute_ex_post(object(), client, user_id=uuid.uuid4())

    assert result.windows == ()
    assert (result.base_date, result.end_date) == (None, None)
    assert result.excluded == twr.excluded
    assert client.calls == []
    assert [r.getMessage() for r in caplog.records] == [
        "ex-post-risk: no TWR returns to measure, so no windows."
    ]


def test_each_window_logs_its_outcome(monkeypatch, caplog):
    _fake_twr(monkeypatch, _twr())
    end = _weekdays(BASE, 530)[-1]

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        flows.compute_ex_post(object(), _FakeDataStoreClient(), user_id=uuid.uuid4())

    messages = [r.getMessage() for r in caplog.records]
    window_lines = [m for m in messages if m.startswith("ex-post-risk: window ")]
    assert len(window_lines) == 5
    assert window_lines[0].startswith(
        f"ex-post-risk: window 1Y ({years_before(end, 1)}..{end}): measured, "
    )
    assert window_lines[1] == (
        f"ex-post-risk: window 3Y ({years_before(end, 3)}..{end}): greyed, insufficient history "
        "(portfolio history from 2020-01-03)."
    )
    assert window_lines[4].startswith(f"ex-post-risk: window Max (2020-01-03..{end}): measured, ")
    assert messages[0] == (
        f"ex-post-risk: measuring 530 day(s) of TWR from 2020-01-03 to {end}, 1 ticker(s)."
    )


def test_a_measured_window_counts_its_greyed_figures(monkeypatch, caplog):
    # 530 weekdays: the 1Y window has ~260 daily obs, under VaR 99%'s minimum of 500, so VaR
    # 99% and CVaR 99% are greyed and the other ten figures shown.
    _fake_twr(monkeypatch, _twr())

    with caplog.at_level(logging.INFO, logger="portfolio.risk.flows"):
        result = flows.compute_ex_post(object(), _FakeDataStoreClient(), user_id=uuid.uuid4())

    one_year = _by_label(result)["1Y"]
    assert Flag.INSUFFICIENT_HISTORY in one_year.tail.var_99.flags
    line = next(r.getMessage() for r in caplog.records if "window 1Y" in r.getMessage())
    assert line.endswith(f"{one_year.drawdown.max_drawdown.n_obs} daily obs, 2 of 12 greyed.")
