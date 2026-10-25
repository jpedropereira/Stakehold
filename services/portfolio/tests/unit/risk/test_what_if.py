"""risk/what_if.py: the candidate what-if (RK-19, spec §7.2).

- "Before" is the holdings with the candidate at weight 0, so before and after share the
  listwise panel: the same window and the same weeks, including weeks lost to the candidate's
  NULLs. If the candidate shortens the window, the report says so.
- The rows lead with the risk deltas (σ_p, diversification ratio, the candidate's share of risk
  and its correlation with the portfolio), then ΔSharpe with Memmel's SE, then max drawdown.
- The threshold SR_c > ρ(c,p)·SR_p is shown under pro-rata funding only, from weekly excess
  returns on the panel. Its test is the derivative it comes from: d SR/dε at ε = 0 of the mix
  (1-ε)·before + ε·c is (σ_c/σ_p)(SR_c - ρ·SR_p).

Calendar: 2015-01-02 is a Friday. Daily fixtures are every weekday after their base, with
returns from a fixed seed.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.ex_ante import constant_weight_series
from portfolio.risk.ex_ante_report import mix_report
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import weekly_excess, weekly_figures
from portfolio.risk.types import Flag, Frequency, ReturnSeries
from portfolio.risk.weekly import to_weekly
from portfolio.risk.what_if import (
    NOT_PRO_RATA,
    PRO_RATA,
    Funding,
    funded_weights,
    what_if_report,
)
from portfolio.risk.windows import StandardWindow

FRIDAY = date(2015, 1, 2)
END = date(2021, 6, 30)  # a Wednesday
FIRST_PUBLISHED = date(1999, 1, 4)
# A rate that moves, so excess returns differ from raw ones by more than a constant.
RATES = [
    RatePoint(FIRST_PUBLISHED, 1.0),
    RatePoint(date(2017, 1, 2), -0.4),
    RatePoint(date(2019, 6, 3), 0.5),
]
EUR = {"A": "EUR", "B": "EUR", "C": "EUR"}
BEFORE = {"A": 0.6, "B": 0.4}


def _daily(base, end, seed, nulls=(), drift=0.0003, scale=0.01):
    dates, day = [], base
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            dates.append(day)
    values = list(np.random.default_rng(seed).normal(drift, scale, len(dates)))
    for k, day in enumerate(dates):
        if day in nulls:
            values[k] = None
    return ReturnSeries(Frequency.DAILY, base, tuple(dates), tuple(values))


def _holdings():
    return {"A": _daily(FRIDAY, END, 1), "B": _daily(FRIDAY, END, 2, scale=0.02)}


def _candidate(base=FRIDAY, nulls=(), drift=0.0006):
    return _daily(base, END, 3, nulls=nulls, drift=drift, scale=0.012)


def _report(
    candidate=None,
    weight=0.1,
    funding=PRO_RATA,
    label=StandardWindow.Y3,
    rates=RATES,
    survivorship=False,
):
    daily = {**_holdings(), "C": _candidate() if candidate is None else candidate}
    return what_if_report(
        daily,
        BEFORE,
        "C",
        weight,
        funding,
        label,
        trading_days={key: set(series.dates) for key, series in daily.items()},
        currencies=EUR,
        rates=rates,
        first_published=None if rates is None else FIRST_PUBLISHED,
        survivorship=survivorship,
    )


def _holdings_alone(label=StandardWindow.Y3):
    holdings = _holdings()
    return mix_report(
        holdings,
        BEFORE,
        label,
        trading_days={key: set(series.dates) for key, series in holdings.items()},
        currencies=EUR,
        rates=RATES,
        first_published=FIRST_PUBLISHED,
    )


# --- funded_weights ------------------------------------------------------------------------------


def test_pro_rata_scales_every_holding_by_one_minus_the_candidates_weight():
    funded = funded_weights(BEFORE, "C", 0.1, PRO_RATA)
    assert funded.after == pytest.approx({"A": 0.54, "B": 0.36, "C": 0.1})
    assert (funded.candidate_weight, funded.capped) == (0.1, False)


def test_replace_moves_the_weight_from_one_holding():
    funded = funded_weights(BEFORE, "C", 0.1, Funding.replace("B"))
    assert funded.after == pytest.approx({"A": 0.6, "B": 0.3, "C": 0.1})
    assert funded.capped is False


def test_replace_caps_the_candidate_at_the_replaced_holdings_weight():
    funded = funded_weights(BEFORE, "C", 0.5, Funding.replace("B"))
    assert funded.after == pytest.approx({"A": 0.6, "B": 0.0, "C": 0.4})
    assert funded.candidate_weight == pytest.approx(0.4)
    assert funded.capped is True


def test_replace_using_the_whole_holding_is_not_capped():
    funded = funded_weights(BEFORE, "C", 0.4, Funding.replace("B"))
    assert funded.after == pytest.approx({"A": 0.6, "B": 0.0, "C": 0.4})
    assert funded.capped is False


@pytest.mark.parametrize(
    "candidate, weight, funding, match",
    [
        ("A", 0.1, PRO_RATA, "already"),
        ("C", 0.0, PRO_RATA, "between 0 and 1"),
        ("C", 1.0, PRO_RATA, "between 0 and 1"),
        ("C", 0.1, Funding.replace("X"), "not a holding"),
    ],
)
def test_funded_weights_refuses_an_invalid_request(candidate, weight, funding, match):
    with pytest.raises(ValueError, match=match):
        funded_weights(BEFORE, candidate, weight, funding)


# --- one panel for before and after --------------------------------------------------------------


def test_a_candidate_covering_the_window_leaves_before_as_the_holdings_alone():
    report = _report()
    alone = _holdings_alone()

    assert report.shortened is False
    assert report.before.window == alone.window == report.after.window
    assert report.sigma_p.before.value == pytest.approx(alone.decomposition.sigma_p.value)
    assert report.diversification_ratio.before.value == pytest.approx(
        alone.decomposition.diversification_ratio.value
    )
    assert report.sharpe.before.value == pytest.approx(alone.constant_weight.weekly.sharpe.value)
    assert report.max_drawdown.before.value == pytest.approx(
        alone.constant_weight.drawdown.max_drawdown.value
    )


def test_a_late_candidate_shortens_the_window_and_before_is_recomputed_on_it():
    late = date(2019, 3, 6)
    report = _report(candidate=_candidate(base=late))
    alone = _holdings_alone()

    assert report.shortened is True
    assert report.without_candidate == alone.window
    assert report.after.window.start > alone.window.start
    assert report.before.window == report.after.window
    assert report.after.limited_by == ("C",)
    assert report.sigma_p.before.n_obs == report.sigma_p.after.n_obs
    assert report.sigma_p.before.n_obs < alone.decomposition.sigma_p.n_obs
    assert report.sigma_p.before.value != pytest.approx(alone.decomposition.sigma_p.value)


def test_a_week_the_candidate_lost_is_dropped_from_before_too():
    report = _report(candidate=_candidate(nulls={date(2020, 3, 11)}))
    alone = _holdings_alone()

    assert report.shortened is False
    assert report.sigma_p.before.n_gaps == alone.decomposition.sigma_p.n_gaps + 1
    assert (report.sigma_p.before.n_obs, report.sigma_p.before.n_gaps) == (
        report.sigma_p.after.n_obs,
        report.sigma_p.after.n_gaps,
    )


# --- the rows ------------------------------------------------------------------------------------


def test_each_row_is_after_minus_before():
    report = _report()
    for row in (report.sigma_p, report.diversification_ratio, report.sharpe, report.max_drawdown):
        assert row.delta == pytest.approx(row.after.value - row.before.value)


def test_the_candidates_rows_come_from_the_two_decompositions():
    report = _report()
    after_c = next(h for h in report.after.decomposition.holdings if h.key == "C")
    before_c = next(h for h in report.before.decomposition.holdings if h.key == "C")

    assert report.candidate_pcr.before is None and report.candidate_pcr.delta is None
    assert report.candidate_pcr.after.value == pytest.approx(after_c.pcr)
    assert report.candidate_correlation.before is None
    assert report.candidate_correlation.after.value == pytest.approx(before_c.corr_with_portfolio)
    assert report.candidate_pcr.after.n_obs == report.sigma_p.after.n_obs


def test_delta_sharpe_carries_memmels_se_far_below_either_sharpes_own():
    report = _report(weight=0.05)
    assert report.sharpe.delta_se is not None
    assert 0 < report.sharpe.delta_se < report.sharpe.before.standard_error / 5


def test_without_a_risk_free_series_the_sharpe_row_is_greyed_and_risk_is_still_measured():
    report = _report(rates=None)
    assert report.sharpe.before.value is None and report.sharpe.after.value is None
    assert report.sharpe.after.reason == NO_RISK_FREE
    assert (report.sharpe.delta, report.sharpe.delta_se) == (None, None)
    assert report.sigma_p.delta is not None


# --- the threshold -------------------------------------------------------------------------------


def _sharpe_of(report, weights):
    weekly = {key: to_weekly(series) for key, series in {**_holdings(), "C": _candidate()}.items()}
    mix = constant_weight_series(weekly, weights)
    return weekly_figures(mix, report.after.window, RATES, FIRST_PUBLISHED).sharpe.value


def _excess_vol(report, weights):
    weekly = {key: to_weekly(series) for key, series in {**_holdings(), "C": _candidate()}.items()}
    x = weekly_excess(
        constant_weight_series(weekly, weights), report.after.window, RATES, FIRST_PUBLISHED
    )
    return np.nanstd(x, ddof=1)


def test_the_threshold_is_the_derivative_of_the_mixs_sharpe_at_zero_weight():
    report = _report()
    threshold = report.threshold
    assert threshold.applicable and threshold.reason is None

    eps = 1e-6
    sr_0 = _sharpe_of(report, {**BEFORE, "C": 0.0})
    sr_eps = _sharpe_of(report, {"A": 0.6 * (1 - eps), "B": 0.4 * (1 - eps), "C": eps})
    slope = (sr_eps - sr_0) / eps

    sigma_c = _excess_vol(report, {"A": 0.0, "B": 0.0, "C": 1.0})
    sigma_p = _excess_vol(report, {**BEFORE, "C": 0.0})
    sr_c = threshold.candidate_sharpe.value
    expected = sigma_c / sigma_p * (sr_c - threshold.correlation * threshold.portfolio_sharpe)
    assert threshold.portfolio_sharpe == pytest.approx(sr_0)
    assert slope == pytest.approx(expected, rel=1e-4)
    assert threshold.threshold == pytest.approx(threshold.correlation * threshold.portfolio_sharpe)
    assert threshold.improves is (slope > 0)


def test_a_weak_candidate_doesnt_clear_the_threshold():
    # Seed 3's noise averages +0.079% a day over the 3Y window, so the drift has to be well
    # below -0.08% for the realised Sharpe to be negative: -0.2% gives about -1.6.
    report = _report(candidate=_candidate(drift=-0.002))
    assert report.threshold.improves is False
    assert report.threshold.candidate_sharpe.value < report.threshold.threshold


def test_the_threshold_isnt_shown_under_replace():
    threshold = _report(funding=Funding.replace("B")).threshold
    assert threshold.applicable is False
    assert threshold.reason == NOT_PRO_RATA
    assert (threshold.candidate_sharpe, threshold.threshold, threshold.improves) == (
        None,
        None,
        None,
    )


def test_the_threshold_greys_without_a_risk_free_series():
    threshold = _report(rates=None).threshold
    assert threshold.applicable is True
    assert threshold.reason == NO_RISK_FREE
    assert (threshold.threshold, threshold.improves) == (None, None)


def test_the_threshold_greys_under_104_weeks_with_the_correlations_reason():
    report = _report(label=StandardWindow.Y1)
    assert report.sigma_p.after.value is not None
    assert report.candidate_correlation.after.value is None
    assert report.threshold.threshold is None
    assert report.threshold.reason == report.candidate_correlation.after.reason
    assert "insufficient history" in report.threshold.reason


# --- survivorship (R14) --------------------------------------------------------------------------


def test_a_candidate_from_the_index_universe_flags_every_after_figure():
    report = _report(survivorship=True)
    rows = (
        report.sigma_p,
        report.diversification_ratio,
        report.candidate_pcr,
        report.candidate_correlation,
        report.sharpe,
        report.max_drawdown,
    )
    for row in rows:
        assert Flag.SURVIVORSHIP in row.after.flags
        assert row.before is None or Flag.SURVIVORSHIP not in row.before.flags
    assert Flag.SURVIVORSHIP in report.threshold.candidate_sharpe.flags


def test_a_tracked_candidate_has_no_survivorship_flag():
    report = _report()
    assert Flag.SURVIVORSHIP not in report.sigma_p.after.flags
    assert Flag.SURVIVORSHIP not in report.threshold.candidate_sharpe.flags
